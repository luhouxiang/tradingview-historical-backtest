package importer

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/html"
	"golang.org/x/text/encoding/simplifiedchinese"
)

const futuresCalendarURL = "https://www.gtjaqh.com/pc/calendar?date=%s"
const tradingDayHistoryURL = "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=20&end=%s&fields1=f1&fields2=f51"

var tdxMarketExchanges = map[string]string{
	"28": "CZCE",
	"29": "DCE",
	"30": "SHFE",
}

type instrumentResolver interface {
	Resolve(context.Context, autoConfigSource) (InstrumentConfig, SessionConfig, error)
	PreviousTradingDay(context.Context, string) (string, string, error)
}

type onlineInstrumentResolver struct {
	client      *http.Client
	calendarURL string
	tradeDayURL string
	now         func() time.Time

	mu           sync.Mutex
	catalog      []remoteInstrument
	catalogURL   string
	catalogDay   string
	catalogReady bool
}

type remoteInstrument struct {
	Exchange           string
	Product            string
	DisplayName        string
	TickSize           string
	ContractMultiplier string
}

func newOnlineInstrumentResolver() *onlineInstrumentResolver {
	return &onlineInstrumentResolver{
		client:      &http.Client{Timeout: 8 * time.Second},
		calendarURL: futuresCalendarURL,
		tradeDayURL: tradingDayHistoryURL,
		now:         time.Now,
	}
}

// Resolve 仅在本地没有对应品种时联网，并把远程交易参数与源文件中实际出现的交易时段合并为完整配置。
func (r *onlineInstrumentResolver) Resolve(ctx context.Context, source autoConfigSource) (InstrumentConfig, SessionConfig, error) {
	product := productFromSymbol(source.Detection.Symbol)
	if product == "" {
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("无法从合约代码 %s 提取品种代码", source.Detection.Symbol)
	}
	exchangeHint := exchangeFromTDXPath(source.Path)
	catalog, sourceURL, sourceDay, err := r.loadCatalog(ctx)
	if err != nil {
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("联网下载期货交易参数失败：%w", err)
	}
	matches := make([]remoteInstrument, 0, 1)
	for _, candidate := range catalog {
		if strings.EqualFold(candidate.Product, product) && (exchangeHint == "" || candidate.Exchange == exchangeHint) {
			matches = append(matches, candidate)
		}
	}
	if len(matches) == 0 {
		label := product
		if exchangeHint != "" {
			label = exchangeHint + "." + product
		}
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("联网交易参数表中没有品种 %s", label)
	}
	if len(matches) > 1 {
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("品种代码 %s 对应多个交易所，无法唯一确定配置", product)
	}
	priceDecimals, priceScale, tickSizeI64, err := fixedPriceParameters(matches[0].TickSize)
	if err != nil {
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("品种 %s 的最小变动价位无效：%w", product, err)
	}
	multiplier, err := positiveInteger(matches[0].ContractMultiplier)
	if err != nil {
		return InstrumentConfig{}, SessionConfig{}, fmt.Errorf("品种 %s 的合约乘数无效：%w", product, err)
	}
	session, err := observedSession(matches[0].Exchange, source, sourceDay)
	if err != nil {
		return InstrumentConfig{}, SessionConfig{}, err
	}
	instrument := InstrumentConfig{
		Exchange: matches[0].Exchange, Product: product,
		SymbolPattern:      "^" + regexp.QuoteMeta(product) + `(?:[0-9]{3,4}|L[0-9])$`,
		DisplayName:        matches[0].DisplayName,
		Timezone:           "Asia/Shanghai",
		PriceDecimals:      priceDecimals,
		PriceScale:         priceScale,
		TickSizeI64:        tickSizeI64,
		ContractMultiplier: multiplier,
		SessionTemplateID:  session.ID,
		RuleSourceURL:      sourceURL,
		RuleVersion:        "gtja-futures-calendar-" + sourceDay,
		RuleCheckedAt:      r.now().In(shanghaiLocation()).Format(time.DateOnly),
	}
	return instrument, session, nil
}

// PreviousTradingDay 从公开日线日期序列中取严格早于目标日期的最后一个交易日，不按自然日直接减一天。
func (r *onlineInstrumentResolver) PreviousTradingDay(ctx context.Context, tradingDay string) (string, string, error) {
	if _, err := time.Parse(time.DateOnly, tradingDay); err != nil {
		return "", "", fmt.Errorf("交易日 %s 格式无效", tradingDay)
	}
	sourceURL := fmt.Sprintf(r.tradeDayURL, url.QueryEscape(strings.ReplaceAll(tradingDay, "-", "")))
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, sourceURL, nil)
	if err != nil {
		return "", "", err
	}
	request.Header.Set("User-Agent", "TVBT/1.0 (+local futures calendar import)")
	response, err := r.client.Do(request)
	if err != nil {
		return "", "", fmt.Errorf("下载交易日序列：%w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("下载交易日序列返回 HTTP %d", response.StatusCode)
	}
	var payload struct {
		Data *struct {
			Klines []string `json:"klines"`
		} `json:"data"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 2<<20)).Decode(&payload); err != nil {
		return "", "", fmt.Errorf("解析交易日序列：%w", err)
	}
	if payload.Data == nil {
		return "", "", fmt.Errorf("交易日序列为空")
	}
	previous := ""
	for _, record := range payload.Data.Klines {
		day, _, _ := strings.Cut(record, ",")
		if day < tradingDay && day > previous {
			previous = day
		}
	}
	if previous == "" {
		return "", "", fmt.Errorf("没有找到 %s 的前一交易日", tradingDay)
	}
	return previous, sourceURL, nil
}

func (r *onlineInstrumentResolver) loadCatalog(ctx context.Context) ([]remoteInstrument, string, string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.catalogReady {
		return r.catalog, r.catalogURL, r.catalogDay, nil
	}
	var failures []string
	for _, day := range recentWeekdays(r.now().In(shanghaiLocation()), 10) {
		dayText := day.Format("20060102")
		sourceURL := fmt.Sprintf(r.calendarURL, url.QueryEscape(dayText))
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, sourceURL, nil)
		if err != nil {
			return nil, "", "", err
		}
		request.Header.Set("User-Agent", "TVBT/1.0 (+local futures metadata import)")
		response, err := r.client.Do(request)
		if err != nil {
			failures = append(failures, dayText+": "+err.Error())
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(response.Body, 16<<20))
		closeErr := response.Body.Close()
		if response.StatusCode != http.StatusOK {
			failures = append(failures, fmt.Sprintf("%s: HTTP %d", dayText, response.StatusCode))
			continue
		}
		if readErr != nil {
			failures = append(failures, dayText+": "+readErr.Error())
			continue
		}
		if closeErr != nil {
			failures = append(failures, dayText+": "+closeErr.Error())
			continue
		}
		catalog, parseErr := parseFuturesCalendar(body)
		if parseErr != nil {
			failures = append(failures, dayText+": "+parseErr.Error())
			continue
		}
		r.catalog = catalog
		r.catalogURL = sourceURL
		r.catalogDay = dayText
		r.catalogReady = true
		return r.catalog, r.catalogURL, r.catalogDay, nil
	}
	if len(failures) == 0 {
		return nil, "", "", fmt.Errorf("最近十个工作日均无可查询日期")
	}
	return nil, "", "", fmt.Errorf("最近十个工作日均未取得有效数据（%s）", strings.Join(failures, "; "))
}

func parseFuturesCalendar(data []byte) ([]remoteInstrument, error) {
	document, err := html.Parse(strings.NewReader(string(data)))
	if err != nil {
		return nil, fmt.Errorf("解析交易参数页面：%w", err)
	}
	rows := make([]remoteInstrument, 0)
	var visit func(*html.Node)
	visit = func(node *html.Node) {
		if node.Type == html.ElementNode && node.Data == "tr" {
			cells := directCellTexts(node)
			if len(cells) >= 8 {
				exchange, knownExchange := normalizeExchange(cells[0])
				product := strings.ToUpper(strings.TrimSpace(cells[2]))
				if knownExchange && product != "" && !strings.Contains(product, "_") && cells[5] != "--" && cells[6] != "--" {
					rows = append(rows, remoteInstrument{
						Exchange: exchange, Product: product, DisplayName: strings.TrimSpace(cells[1]),
						ContractMultiplier: strings.TrimSpace(cells[5]), TickSize: strings.TrimSpace(cells[6]),
					})
				}
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(document)
	if len(rows) == 0 {
		return nil, fmt.Errorf("页面中没有期货品种交易参数")
	}
	return rows, nil
}

func directCellTexts(row *html.Node) []string {
	cells := make([]string, 0, 10)
	for child := row.FirstChild; child != nil; child = child.NextSibling {
		if child.Type == html.ElementNode && (child.Data == "td" || child.Data == "th") {
			cells = append(cells, nodeText(child))
		}
	}
	return cells
}

func nodeText(node *html.Node) string {
	parts := make([]string, 0, 2)
	var visit func(*html.Node)
	visit = func(current *html.Node) {
		if current.Type == html.TextNode {
			parts = append(parts, current.Data)
		}
		for child := current.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(node)
	return strings.Join(strings.Fields(strings.Join(parts, " ")), " ")
}

func normalizeExchange(value string) (string, bool) {
	exchanges := map[string]string{
		"上期所": "SHFE", "能源中心": "INE", "大商所": "DCE", "郑商所": "CZCE",
		"中金所": "CFFEX", "广期所": "GFEX",
	}
	exchange, ok := exchanges[strings.TrimSpace(value)]
	return exchange, ok
}

func fixedPriceParameters(value string) (int, int64, int64, error) {
	normalized := strings.ReplaceAll(strings.TrimSpace(value), ",", "")
	if normalized == "" {
		return 0, 0, 0, fmt.Errorf("值为空")
	}
	decimals := 0
	if dot := strings.IndexByte(normalized, '.'); dot >= 0 {
		fraction := strings.TrimRight(normalized[dot+1:], "0")
		decimals = len(fraction)
		normalized = normalized[:dot+1] + fraction
		if fraction == "" {
			normalized = normalized[:dot]
		}
	}
	if decimals > 18 {
		return 0, 0, 0, fmt.Errorf("小数位超过 18 位")
	}
	scale := int64(1)
	for range decimals {
		scale *= 10
	}
	tick, err := parseFixed(normalized, decimals, scale)
	if err != nil || tick < 1 {
		return 0, 0, 0, fmt.Errorf("%q 不是正数", value)
	}
	return decimals, scale, tick, nil
}

func positiveInteger(value string) (int64, error) {
	normalized := strings.ReplaceAll(strings.TrimSpace(value), ",", "")
	number, err := strconv.ParseFloat(normalized, 64)
	if err != nil || number < 1 || number != float64(int64(number)) {
		return 0, fmt.Errorf("%q 不是正整数", value)
	}
	return int64(number), nil
}

func observedSession(exchange string, source autoConfigSource, checkedDay string) (SessionConfig, error) {
	decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(source.Data)
	if err != nil {
		return SessionConfig{}, fmt.Errorf("读取交易时段失败：%w", err)
	}
	timeframe, err := strconv.Atoi(strings.TrimSuffix(source.Detection.Timeframe, "m"))
	if err != nil || timeframe < 1 {
		return SessionConfig{}, fmt.Errorf("无法从周期 %s 确定交易时段", source.Detection.Timeframe)
	}
	lateMinutes := make([]int, 0)
	afterMidnightMinutes := make([]int, 0)
	for _, line := range splitLines(decoded)[2:] {
		fields := strings.Split(strings.TrimSpace(line), ",")
		if len(fields) != 9 {
			continue
		}
		hhmm, parseErr := parseHHMM(fields[1])
		if parseErr != nil {
			continue
		}
		minutes := hhmm/100*60 + hhmm%100
		if minutes >= 18*60 {
			lateMinutes = append(lateMinutes, minutes)
		} else if minutes <= 6*60 {
			afterMidnightMinutes = append(afterMidnightMinutes, minutes)
		}
	}
	segments := standardCommodityDaySegments()
	session := SessionConfig{
		Timezone: "Asia/Shanghai", Segments: segments,
		RuleVersion:   "tdx-observed-session-v1-" + checkedDay,
		RuleCheckedAt: checkedDay[:4] + "-" + checkedDay[4:6] + "-" + checkedDay[6:],
	}
	if len(lateMinutes) == 0 {
		session.ID = strings.ToLower(exchange) + "_futures_day"
		return session, nil
	}
	sort.Ints(lateMinutes)
	sort.Ints(afterMidnightMinutes)
	startMinutes := lateMinutes[0] - timeframe
	if startMinutes < 18*60 {
		return SessionConfig{}, fmt.Errorf("源文件中的夜盘起点无法可靠识别")
	}
	nightStart := formatMinutes(startMinutes)
	nightEnd := formatMinutes(lateMinutes[len(lateMinutes)-1])
	nightSegments := []SessionSegment{{Name: "night", Start: nightStart, End: nightEnd, CalendarDateRule: "night_session_date"}}
	if len(afterMidnightMinutes) > 0 {
		nightEnd = formatMinutes(afterMidnightMinutes[len(afterMidnightMinutes)-1])
		nightSegments = []SessionSegment{
			{Name: "night_before_midnight", Start: nightStart, End: "24:00", CalendarDateRule: "night_session_date"},
			{Name: "night_after_midnight", Start: "00:00", End: nightEnd, CalendarDateRule: "trading_day"},
		}
	}
	session.ID = strings.ToLower(exchange) + "_futures_night_" + strings.ReplaceAll(nightEnd, ":", "")
	session.NightStart = nightStart
	session.NightEnd = nightEnd
	session.Segments = append(nightSegments, segments...)
	return session, nil
}

func standardCommodityDaySegments() []SessionSegment {
	return []SessionSegment{
		{Name: "day_1", Start: "09:00", End: "10:15", CalendarDateRule: "trading_day"},
		{Name: "day_2", Start: "10:30", End: "11:30", CalendarDateRule: "trading_day"},
		{Name: "day_3", Start: "13:30", End: "15:00", CalendarDateRule: "trading_day"},
	}
}

func formatMinutes(minutes int) string {
	return fmt.Sprintf("%02d:%02d", minutes/60, minutes%60)
}

func exchangeFromTDXPath(path string) string {
	name := filepath.Base(path)
	market, _, found := strings.Cut(name, "#")
	if !found {
		return ""
	}
	return tdxMarketExchanges[market]
}

func recentWeekdays(now time.Time, count int) []time.Time {
	result := make([]time.Time, 0, count)
	for day := now; len(result) < count; day = day.AddDate(0, 0, -1) {
		if day.Weekday() != time.Saturday && day.Weekday() != time.Sunday {
			result = append(result, day)
		}
	}
	return result
}

func shanghaiLocation() *time.Location {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	return location
}
