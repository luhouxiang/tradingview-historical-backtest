package importer

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	appconfig "github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
	"golang.org/x/text/encoding/simplifiedchinese"
)

type stubInstrumentResolver struct {
	resolveCalls  int
	previousCalls int
	resolve       func(context.Context, autoConfigSource) (InstrumentConfig, SessionConfig, error)
	previous      func(context.Context, string) (string, string, error)
}

func (r *stubInstrumentResolver) Resolve(ctx context.Context, source autoConfigSource) (InstrumentConfig, SessionConfig, error) {
	r.resolveCalls++
	if r.resolve == nil {
		return InstrumentConfig{}, SessionConfig{}, errors.New("测试不允许查询品种配置")
	}
	return r.resolve(ctx, source)
}

func (r *stubInstrumentResolver) PreviousTradingDay(ctx context.Context, day string) (string, string, error) {
	r.previousCalls++
	if r.previous == nil {
		return "", "", errors.New("测试不允许查询前一交易日")
	}
	return r.previous(ctx, day)
}

func TestDetectFullSample(t *testing.T) {
	// 测试一键启动历史数据源中的完整 AOL9 文件识别；期望标题、周期和编码全部正确。
	data := readCanonicalHistory(t, "30#AOL9.txt")
	detection, err := DetectTdx(data)
	if err != nil {
		t.Fatal(err)
	}
	if detection.Symbol != "AOL9" || detection.Timeframe != "5m" || detection.Encoding != "GB18030" {
		t.Fatalf("unexpected detection: %#v", detection)
	}
}

func TestDetectContinuousContractSample(t *testing.T) {
	// 测试通达信加权指数标题识别；期望保留指数代码、中文名称和五分钟周期。
	data, err := simplifiedchinese.GB18030.NewEncoder().Bytes([]byte(
		"AOL9 氧化铝加权 5分钟线 不复权\n日期 时间 开盘 最高 最低 收盘 成交量 持仓量 结算价\n2026/08/03,0100,2650,2652,2648,2651,1832,567435,0\n",
	))
	if err != nil {
		t.Fatal(err)
	}
	detection, err := DetectTdx(data)
	if err != nil {
		t.Fatal(err)
	}
	if detection.Symbol != "AOL9" || detection.DisplayName != "氧化铝加权" || detection.Timeframe != "5m" {
		t.Fatalf("unexpected detection: %#v", detection)
	}
}

func TestContractAndIndexShareOneProductConfig(t *testing.T) {
	// 测试具体合约与加权指数的品种回退匹配；期望 AO2609 和 AOL9 共用唯一的 AO 配置。
	instrument := InstrumentConfig{
		Exchange: "SHFE", Product: "AO", SymbolPattern: `^AO[0-9]{4}$`, DisplayName: "氧化铝",
		Timezone: "Asia/Shanghai", PriceDecimals: 0, PriceScale: 1, TickSizeI64: 1,
		ContractMultiplier: 20, SessionTemplateID: "cn_futures_night_0100",
	}
	document := instrumentFile{SchemaVersion: 1, Instruments: []InstrumentConfig{instrument}}
	contract, contractOK := instrumentFromFile(document, "AO2609")
	index, indexOK := instrumentFromFile(document, "AOL9")
	if !contractOK || !indexOK || contract.Product != "AO" || index.Product != "AO" {
		t.Fatalf("contract and index did not share AO config: contract=%#v index=%#v", contract, index)
	}
	if len(document.Instruments) != 1 {
		t.Fatalf("product config count = %d, want 1", len(document.Instruments))
	}
}

func TestProductFallbackStillRequiresSameExchange(t *testing.T) {
	// 测试不同交易所出现相同品种字母时的回退匹配；期望只有交易所与品种都一致才允许复用配置。
	document := instrumentFile{SchemaVersion: 1, Instruments: []InstrumentConfig{{
		Exchange: "DCE", Product: "Y", SymbolPattern: `^Y(?:[0-9]{4}|L9)$`,
	}}}
	if !instrumentFileMatches(document, "DCE", "YL9") {
		t.Fatal("same exchange and product should reuse configuration")
	}
	if instrumentFileMatches(document, "SHFE", "YL9") {
		t.Fatal("different exchange must not reuse product configuration")
	}
}

func TestAOListingCalendarSeedMapsFirstIndexNight(t *testing.T) {
	// 测试 AOL9 从上市首个夜盘开始时的固定日历种子；期望使用上期所上市日映射且无需第二条指数配置。
	instruments := instrumentFile{SchemaVersion: 1, Instruments: []InstrumentConfig{{
		Exchange: "SHFE", Product: "AO", SymbolPattern: `^AO[0-9]{4}$`, DisplayName: "氧化铝",
		Timezone: "Asia/Shanghai", PriceDecimals: 0, PriceScale: 1, TickSizeI64: 1,
		ContractMultiplier: 20, SessionTemplateID: "cn_futures_night_0100",
	}}}
	sessions := sessionFile{SchemaVersion: 1, Templates: []SessionConfig{{
		ID: "cn_futures_night_0100", Timezone: "Asia/Shanghai", NightStart: "21:00", NightEnd: "01:00",
	}}}
	data := encodeFixture(t, `AOL9 氧化铝加权 5分钟线 不复权
      日期      时间      开盘      最高      最低      收盘      成交量      持仓量      结算价
2023/06/20,2105,2702,2716,2702,2710,3800,15113,0
2023/06/20,0100,2710,2712,2708,2711,100,15120,0
`)
	calendar := make(map[string]CalendarEntry)
	changed, err := supplementCalendar(context.Background(), calendar, []autoConfigSource{{
		Detection: Detection{Symbol: "AOL9"}, Data: data,
	}}, instruments, sessions, nil)
	if err != nil {
		t.Fatal(err)
	}
	entry := calendar["2023-06-20"]
	if !changed || entry.NightSessionDate != "2023-06-19" || len(instruments.Instruments) != 1 {
		t.Fatalf("unexpected AO listing calendar seed: changed=%v entry=%#v", changed, entry)
	}
}

func TestDetectZhengzhouThreeDigitContract(t *testing.T) {
	// 测试郑商所三位合约月份代码可以被识别；期望 SR701 不再因位数不同而被拒绝。
	data := encodeFixture(t, strings.Replace(validFixture(), "AO2609 氧化铝2609", "SR701 白糖701", 1))
	detection, err := DetectTdx(data)
	if err != nil {
		t.Fatal(err)
	}
	if detection.Symbol != "SR701" || detection.DisplayName != "白糖701" {
		t.Fatalf("unexpected detection: %#v", detection)
	}
}

func TestFullSampleImportsToParquetWithinTarget(t *testing.T) {
	// 测试一键启动历史数据源中的完整 AOL9 导入；期望行数、质量统计和性能满足验收值。
	data := readCanonicalHistory(t, "30#AOL9.txt")
	instrument := InstrumentConfig{
		Exchange: "SHFE", Product: "AO", Timezone: "Asia/Shanghai", PriceDecimals: 0, PriceScale: 1,
		TickSizeI64: 1, SessionTemplateID: "sample",
	}
	session := SessionConfig{
		ID: "sample", Timezone: "Asia/Shanghai", NightStart: "21:00", NightEnd: "01:00", nightHHMM: 2100,
		Segments: []SessionSegment{
			{Name: "night_before", Start: "21:00", End: "24:00"},
			{Name: "night_after", Start: "00:00", End: "01:00"},
			{Name: "day_1", Start: "09:00", End: "10:15"},
			{Name: "day_2", Start: "10:30", End: "11:30"},
			{Name: "day_3", Start: "13:30", End: "15:00"},
		},
	}
	calendar := canonicalCalendar(t)
	started := time.Now()
	result, err := ParseTdx(data, "history/30#AOL9.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai",
		FailOnDuplicate: true, KeepZeroVolumeBars: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Bars) < 69289 || result.Quality.Counts["error_count"] != 0 {
		t.Fatalf("full sample counts: bars=%d quality=%#v", len(result.Bars), result.Quality.Counts)
	}
	path := filepath.Join(t.TempDir(), "bars.parquet")
	revision := hashBytes(data)
	if err := writeBars(path, result.Bars, "SHFE.AOL9.5m", revision, ImportRequest{Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end"}, instrument, 32768); err != nil {
		t.Fatal(err)
	}
	elapsed := time.Since(started)
	t.Logf("%d-bar parse and Parquet write: %s", len(result.Bars), elapsed)
	if elapsed > 5*time.Second {
		t.Fatalf("full sample import exceeded 5s target: %s", elapsed)
	}
}

func readCanonicalHistory(t *testing.T, name string) []byte {
	t.Helper()
	path := filepath.Join("..", "..", "trading-data", "history", name)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		t.Skipf("唯一历史数据源中不存在完整测试文件：%s", path)
	}
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func canonicalCalendar(t *testing.T) map[string]CalendarEntry {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "trading-data", "config", "trading_calendar.csv"))
	if err != nil {
		t.Fatal(err)
	}
	calendar, err := parseCalendar(data)
	if err != nil {
		t.Fatal(err)
	}
	return calendar
}

func TestParseMapsNightSessionThroughCalendar(t *testing.T) {
	// 测试夜盘 K 线通过显式交易日历换算自然日期；期望夜盘时间戳落在前一交易日对应的自然日。
	data := encodeFixture(t, validFixture())
	instrument, session, calendar := testRuntimeParts(t)
	result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai",
		FailOnDuplicate: true, KeepZeroVolumeBars: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	location, _ := time.LoadLocation("Asia/Shanghai")
	wantFirst := time.Date(2025, 9, 19, 21, 45, 0, 0, location).UTC().UnixMilli()
	wantThird := time.Date(2025, 9, 22, 1, 0, 0, 0, location).UTC().UnixMilli()
	if result.Bars[0].TimestampUTC != wantFirst || result.Bars[2].TimestampUTC != wantThird {
		t.Fatalf("night mapping = %d/%d, want %d/%d", result.Bars[0].TimestampUTC, result.Bars[2].TimestampUTC, wantFirst, wantThird)
	}
	if result.Bars[3].Flags&FlagZeroVolume == 0 || result.Bars[3].SettlementI64 != nil {
		t.Fatalf("zero-volume/missing settlement flags not preserved: %#v", result.Bars[3])
	}
	for index, bar := range result.Bars {
		if bar.BarIndex != int64(index) || index > 0 && bar.TimestampUTC <= result.Bars[index-1].TimestampUTC {
			t.Fatalf("bars are not strictly increasing: %#v", result.Bars)
		}
	}
}

func TestParseFailsWithoutCalendarMapping(t *testing.T) {
	// 测试夜盘源缺失交易日历映射；期望导入明确失败，不能把交易日直接当作夜盘自然日期。
	data := encodeFixture(t, validFixture())
	instrument, session, _ := testRuntimeParts(t)
	result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, map[string]CalendarEntry{}, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai", KeepZeroVolumeBars: true,
	})
	if err == nil || result.Quality.Counts["error_count"] == 0 {
		t.Fatalf("expected calendar error, got %v / %#v", err, result.Quality)
	}
	if result.Quality.Issues[0].SourceLine != 3 {
		t.Fatalf("source line = %d", result.Quality.Issues[0].SourceLine)
	}
}

func TestLongHolidayNightUsesExplicitCalendarDate(t *testing.T) {
	// 测试长假后首个交易日的夜盘映射；期望使用配置中的前一开市日，不按自然日减一天。
	location, _ := time.LoadLocation("Asia/Shanghai")
	session := SessionConfig{nightHHMM: 2100}
	sourceDate, _ := time.Parse(time.DateOnly, "2025-10-09")
	got, err := mapTimestamp(sourceDate, "2025-10-09", 2105, "trading_day", session, map[string]CalendarEntry{
		"2025-10-09": {TradingDay: "2025-10-09", NightSessionDate: "2025-09-30", IsOpen: true},
	}, location)
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2025, 9, 30, 21, 5, 0, 0, location).UTC().UnixMilli()
	if got != want {
		t.Fatalf("holiday mapping = %d, want %d", got, want)
	}
}

func TestDayOnlySessionDoesNotRequireNightMapping(t *testing.T) {
	// 测试纯日盘时段的时间换算；期望无需夜盘映射也能生成当天的 UTC 时间戳。
	location, _ := time.LoadLocation("Asia/Shanghai")
	session := SessionConfig{nightHHMM: 2400}
	sourceDate, _ := time.Parse(time.DateOnly, "2025-09-22")
	got, err := mapTimestamp(sourceDate, "2025-09-22", 905, "trading_day", session, nil, location)
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2025, 9, 22, 9, 5, 0, 0, location).UTC().UnixMilli()
	if got != want {
		t.Fatalf("day-only mapping = %d, want %d", got, want)
	}
}

func TestParseReportsInvalidOHLCSourceLine(t *testing.T) {
	// 测试最高价低于开盘价的非法 OHLC；期望质量报告指出准确的原始文件行号。
	fixture := strings.Replace(validFixture(), "3108,3110,3107,3109", "3108,3100,3107,3109", 1)
	data := encodeFixture(t, fixture)
	instrument, session, calendar := testRuntimeParts(t)
	result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai", KeepZeroVolumeBars: true,
	})
	if err == nil || result.Quality.Counts["invalid_ohlc_count"] != 1 {
		t.Fatalf("expected invalid OHLC, got %v / %#v", err, result.Quality)
	}
	if result.Quality.Issues[0].SourceLine != 3 {
		t.Fatalf("source line = %d", result.Quality.Issues[0].SourceLine)
	}
}

func TestImportIsImmutableAndRevisionAware(t *testing.T) {
	// 测试原始文件只读、标准化提交和版本复用；期望原文件哈希不变且相同修订不会重复写入。
	service, guard, store, sourcePath := newTestService(t)
	sourceBefore, _ := os.ReadFile(sourcePath)
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Status != "importable" {
		t.Fatalf("unexpected scan: %#v", items)
	}
	request := ImportRequest{
		SourceFileID: items[0].SourceFileID, ImporterID: AdapterID, Exchange: "SHFE", Instrument: "AO2609", Timeframe: "5m",
		DateSemantics: "trading_day", Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end",
	}
	meta, reused, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil || reused {
		t.Fatalf("first import: reused=%v err=%v", reused, err)
	}
	if meta.Coverage.BarCount != 5 || meta.Coverage.TradingDayCount != 1 || meta.IndependenceGroup != "SHFE.AO" || meta.Quality.ZeroVolumeCount != 1 {
		t.Fatalf("unexpected metadata: %#v", meta)
	}
	apiMeta, err := service.GetDataset(meta.DatasetID, meta.DataRevision)
	if err != nil || apiMeta.Instrument.ContractMultiplier != 20 {
		t.Fatalf("dataset API did not expose authoritative contract multiplier: %#v err=%v", apiMeta.Instrument, err)
	}
	sourceAfter, _ := os.ReadFile(sourcePath)
	if !bytes.Equal(sourceBefore, sourceAfter) {
		t.Fatal("source file was modified")
	}
	second, reused, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil || !reused || second.DataRevision != meta.DataRevision {
		t.Fatalf("repeat import: %#v reused=%v err=%v", second, reused, err)
	}
	items = service.SourceFiles()
	if len(items) != 1 || items[0].Status != "imported" {
		t.Fatalf("source status after import: %#v", items)
	}
	barsPath, err := guard.Resolve(meta.Files[0].Path)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := parquet.ReadFile[Bar](barsPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 5 || rows[0].TimestampUTC >= rows[1].TimestampUTC {
		t.Fatalf("unexpected parquet rows: %#v", rows)
	}
	file, err := os.Open(barsPath)
	if err != nil {
		t.Fatal(err)
	}
	info, _ := file.Stat()
	parquetFile, err := parquet.OpenFile(file, info.Size())
	if err != nil {
		t.Fatal(err)
	}
	if value, ok := parquetFile.Lookup("data_revision"); !ok || value != meta.DataRevision {
		t.Fatalf("parquet revision = %q/%v", value, ok)
	}
	if !strings.Contains(parquetFile.Schema().String(), "(DATE)") {
		t.Fatalf("trading_day is not a date logical type: %s", parquetFile.Schema())
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	document, metas, err := store.List()
	if err != nil || document.CatalogRevision != 1 || len(metas) != 1 {
		t.Fatalf("catalog = %#v, metas=%d, err=%v", document, len(metas), err)
	}
	successPath := filepath.Join(filepath.Dir(barsPath), "_SUCCESS")
	if err := os.Remove(successPath); err != nil {
		t.Fatal(err)
	}
	_, metas, _ = store.List()
	if len(metas) != 0 {
		t.Fatal("incomplete revision entered ready catalog results")
	}
}

func TestImportIgnoresChartDisplayBoundsAndVersionsIndependenceOverride(t *testing.T) {
	service, _, _, _ := newTestService(t)
	service.config.Chart.BeginDT = "2099-01-01 00:00:00"
	service.config.Chart.EndDT = "2099-01-02 00:00:00"
	items, err := service.Scan(context.Background())
	if err != nil || len(items) != 1 {
		t.Fatalf("scan: %#v, %v", items, err)
	}
	request := ImportRequest{
		SourceFileID: items[0].SourceFileID, ImporterID: AdapterID, Exchange: "SHFE", Instrument: "AO2609", Timeframe: "5m",
		DateSemantics: "trading_day", Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end", IndependenceGroup: "CUSTOM.AO",
	}
	meta, _, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil {
		t.Fatal(err)
	}
	if meta.Coverage.BarCount != 5 || meta.IndependenceGroup != "CUSTOM.AO" {
		t.Fatalf("chart bounds truncated import or group was lost: %#v", meta)
	}
	request.IndependenceGroup = "CUSTOM.METAL"
	second, reused, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil || reused || second.DataRevision == meta.DataRevision {
		t.Fatalf("independence override did not version metadata: reused=%v first=%s second=%s err=%v", reused, meta.DataRevision, second.DataRevision, err)
	}
}

func TestScanAutomaticallyConfiguresAndImportsSR701(t *testing.T) {
	// 测试空运行配置下扫描白糖合约；期望自动生成品种、23 点收盘时段和夜盘交易日映射。
	service, guard, sourcePath := newAutomaticConfigService(t, sr701Fixture())
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Status != "importable" {
		t.Fatalf("SR701 should be importable after automatic configuration: %#v", items)
	}
	if items[0].Detected["exchange"] != "CZCE" || items[0].Detected["timezone"] != "Asia/Shanghai" {
		t.Fatalf("unexpected generated mapping: %#v", items[0].Detected)
	}

	config, err := loadRuntimeConfig(guard)
	if err != nil {
		t.Fatal(err)
	}
	instrument, ok := config.instrument("CZCE", "SR701")
	if !ok || instrument.PriceScale != 1 || instrument.TickSizeI64 != 1 || instrument.ContractMultiplier != 10 {
		t.Fatalf("unexpected SR instrument config: %#v", instrument)
	}
	if instrument.RuleSourceURL == "" || instrument.RuleVersion != "czce-sr-2024-06-26" {
		t.Fatalf("generated rule provenance is missing: %#v", instrument)
	}
	if got := config.calendar["2026-01-05"].NightSessionDate; got != "2026-01-02" {
		t.Fatalf("night session date = %q, want 2026-01-02", got)
	}

	request := ImportRequest{
		SourceFileID: items[0].SourceFileID, ImporterID: AdapterID, Exchange: "CZCE", Instrument: "SR701", Timeframe: "5m",
		DateSemantics: "trading_day", Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end",
	}
	meta, reused, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil || reused {
		t.Fatalf("SR701 import: reused=%v err=%v", reused, err)
	}
	if meta.DatasetID != "CZCE.SR701.5m" || meta.Price.PriceScale != 1 || meta.Price.TickSizeI64 != 1 {
		t.Fatalf("unexpected SR701 metadata: %#v", meta)
	}
	sourceAfter, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(sourceAfter, encodeFixture(t, sr701Fixture())) {
		t.Fatal("automatic configuration modified the original TDX file")
	}

	// 测试重复扫描的幂等性；期望三个配置文件内容保持完全一致，不产生重复条目。
	before := readRuntimeConfigFiles(t, guard)
	if _, err := service.Scan(context.Background()); err != nil {
		t.Fatal(err)
	}
	after := readRuntimeConfigFiles(t, guard)
	for name, data := range before {
		if !bytes.Equal(data, after[name]) {
			t.Fatalf("runtime config %s changed during repeated scan", name)
		}
	}
}

func TestAutomaticCalendarRejectsUnprovableFirstNightSession(t *testing.T) {
	// 测试文件从首个夜盘交易日开始且联网日历不可用；期望不猜测自然日期，并保持待映射状态。
	fixture := `SR701 白糖701 5分钟线 不复权
      日期      时间      开盘      最高      最低      收盘      成交量      持仓量      结算价
2026/01/05,2105,6011,6013,6010,6012,12,101,6011
2026/01/05,0905,6013,6015,6012,6014,15,103,6013
2026/01/05,1500,6014,6016,6013,6015,20,104,6014
`
	service, _, _ := newAutomaticConfigService(t, fixture)
	service.resolver = &stubInstrumentResolver{previous: func(context.Context, string) (string, string, error) {
		return "", "", errors.New("模拟交易日服务不可用")
	}}
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Status != "needs_mapping" {
		t.Fatalf("source without a provable previous trading day must not be importable: %#v", items)
	}
	if len(items[0].Issues) != 1 || items[0].Issues[0].Code != "TRADING_CALENDAR_MAPPING_MISSING" {
		t.Fatalf("unexpected calendar issue: %#v", items[0].Issues)
	}
}

func TestUnknownInstrumentReportsMissingAuthoritativeMetadata(t *testing.T) {
	// 测试本地规则未覆盖且联网查询失败的合约；期望保留明确失败原因，不猜测任何品种语义。
	fixture := strings.Replace(sr701Fixture(), "SR701 白糖701", "XX701 未知品种701", 1)
	service, guard, _ := newAutomaticConfigService(t, fixture)
	service.resolver = &stubInstrumentResolver{resolve: func(context.Context, autoConfigSource) (InstrumentConfig, SessionConfig, error) {
		return InstrumentConfig{}, SessionConfig{}, errors.New("联网交易参数表中没有品种 SHFE.XX")
	}}
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Status != "needs_mapping" {
		t.Fatalf("unknown instrument must remain unmapped: %#v", items)
	}
	if len(items[0].Issues) != 1 || items[0].Issues[0].Code != "INSTRUMENT_METADATA_LOOKUP_FAILED" {
		t.Fatalf("unexpected instrument issue: %#v", items[0].Issues)
	}
	if !strings.Contains(items[0].Issues[0].Message, "SHFE.XX") {
		t.Fatalf("lookup failure must retain the concrete product: %#v", items[0].Issues)
	}
	for name := range readRuntimeConfigFiles(t, guard) {
		if name == "" {
			t.Fatal("runtime config file name must not be empty")
		}
	}
}

func TestAutomaticConfigurationPreservesExistingMapping(t *testing.T) {
	// 测试用户已经提供完整 SR 映射的情况；期望扫描逐字保留三个配置文件，并且完全不联网。
	service, guard, _ := newAutomaticConfigService(t, sr701Fixture())
	resolver := &stubInstrumentResolver{}
	service.resolver = resolver
	configDir, err := guard.Resolve("config")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(configDir, 0o750); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{
		"instruments.json":     `{"schema_version":1,"instruments":[{"exchange":"CZCE","product":"SR","symbol_pattern":"^SR[0-9]{3}$","display_name":"自定义白糖","timezone":"Asia/Shanghai","price_decimals":0,"price_scale":1,"tick_size_i64":1,"contract_multiplier":10,"session_template_id":"custom_sr"}]}`,
		"sessions.json":        `{"schema_version":1,"templates":[{"id":"custom_sr","timezone":"Asia/Shanghai","night_start":"21:00","night_end":"23:00","segments":[{"name":"night","start":"21:00","end":"23:00","calendar_date_rule":"night_session_date"},{"name":"day","start":"09:00","end":"15:00","calendar_date_rule":"trading_day"}]}]}`,
		"trading_calendar.csv": "trading_day,night_session_date,is_open,note\n2026-01-05,2026-01-02,true,custom\n",
	}
	for name, data := range files {
		if err := os.WriteFile(filepath.Join(configDir, name), []byte(data), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	before := readRuntimeConfigFiles(t, guard)
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].Status != "importable" {
		t.Fatalf("existing mapping should remain importable: %#v", items)
	}
	after := readRuntimeConfigFiles(t, guard)
	for name, data := range before {
		if !bytes.Equal(data, after[name]) {
			t.Fatalf("existing runtime config %s was overwritten", name)
		}
	}
	if resolver.resolveCalls != 0 || resolver.previousCalls != 0 {
		t.Fatalf("existing product configuration triggered network resolver: %#v", resolver)
	}
}

func TestOnlineInstrumentResolverReadsYAndPreviousTradingDay(t *testing.T) {
	// 测试联网解析豆油参数与首个夜盘的前一交易日；期望取得 DCE.Y、乘数 10、最小变动 1 和 23 点夜盘。
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/calendar":
			writer.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = writer.Write([]byte(futuresCalendarFixture()))
		case "/trading-days":
			writer.Header().Set("Content-Type", "application/json")
			_, _ = writer.Write([]byte(`{"data":{"klines":["2022-12-09","2022-12-12","2022-12-13"]}}`))
		default:
			http.NotFound(writer, request)
		}
	}))
	defer server.Close()
	resolver := newOnlineInstrumentResolver()
	resolver.client = server.Client()
	resolver.calendarURL = server.URL + "/calendar?date=%s"
	resolver.tradeDayURL = server.URL + "/trading-days?end=%s"
	resolver.now = func() time.Time { return time.Date(2026, 8, 16, 12, 0, 0, 0, shanghaiLocation()) }
	source := autoConfigSource{
		Path: "history/29#YL9.txt", Detection: Detection{Symbol: "YL9", Timeframe: "5m"},
		Data: encodeFixture(t, yL9Fixture()),
	}
	instrument, session, err := resolver.Resolve(context.Background(), source)
	if err != nil {
		t.Fatal(err)
	}
	if instrument.Exchange != "DCE" || instrument.Product != "Y" || instrument.DisplayName != "豆油" {
		t.Fatalf("unexpected Y instrument identity: %#v", instrument)
	}
	if instrument.PriceDecimals != 0 || instrument.PriceScale != 1 || instrument.TickSizeI64 != 1 || instrument.ContractMultiplier != 10 {
		t.Fatalf("unexpected Y price parameters: %#v", instrument)
	}
	if session.ID != "dce_futures_night_2300" || session.NightStart != "21:00" || session.NightEnd != "23:00" {
		t.Fatalf("unexpected Y session: %#v", session)
	}
	previous, _, err := resolver.PreviousTradingDay(context.Background(), "2022-12-13")
	if err != nil || previous != "2022-12-12" {
		t.Fatalf("previous trading day = %q, err=%v", previous, err)
	}
}

func TestScanDownloadsOneProductConfigurationForYContractAndIndex(t *testing.T) {
	// 测试豆油具体合约与加权指数同时首次出现；期望只联网解析一次，并生成唯一一条 DCE.Y 品种配置供两者复用。
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = writer.Write([]byte(futuresCalendarFixture()))
	}))
	defer server.Close()
	service, guard, _ := newAutomaticConfigServiceWithName(t, "29#YL9.txt", yL9Fixture())
	historyDir, err := guard.Resolve("history")
	if err != nil {
		t.Fatal(err)
	}
	contractFixture := strings.Replace(yL9Fixture(), "YL9 豆油加权", "Y2609 豆油2609", 1)
	if err := os.WriteFile(filepath.Join(historyDir, "29#Y2609.txt"), encodeFixture(t, contractFixture), 0o640); err != nil {
		t.Fatal(err)
	}
	online := newOnlineInstrumentResolver()
	online.client = server.Client()
	online.calendarURL = server.URL + "/calendar?date=%s"
	online.now = func() time.Time { return time.Date(2026, 8, 14, 12, 0, 0, 0, shanghaiLocation()) }
	resolver := &stubInstrumentResolver{
		resolve: online.Resolve,
		previous: func(context.Context, string) (string, string, error) {
			return "", "", errors.New("测试数据已有可证明的前一交易日")
		},
	}
	service.resolver = resolver
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 || items[0].Status != "importable" || items[1].Status != "importable" {
		t.Fatalf("Y contract and index should both be importable: %#v", items)
	}
	if resolver.resolveCalls != 1 || resolver.previousCalls != 0 {
		t.Fatalf("same product should resolve exactly once: %#v", resolver)
	}
	document, err := readInstrumentFile(filepath.Join(filepath.Dir(historyDir), "config", "instruments.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(document.Instruments) != 1 {
		t.Fatalf("generated instrument count = %d, want 1", len(document.Instruments))
	}
	if _, ok := instrumentFromFile(document, "Y2609"); !ok {
		t.Fatal("concrete Y contract did not reuse generated product configuration")
	}
	if _, ok := instrumentFromFile(document, "YL9"); !ok {
		t.Fatal("Y weighted index did not reuse generated product configuration")
	}
}

func TestFixedPriceParametersCoverIntegerAndDecimalTicks(t *testing.T) {
	// 测试整数、半点、四分之一点和带尾零的最小变动价位；期望价格倍率始终等于 10 的有效小数位次幂。
	tests := []struct {
		value              string
		decimals           int
		scale, tickSizeI64 int64
	}{
		{value: "1", decimals: 0, scale: 1, tickSizeI64: 1},
		{value: "0.5", decimals: 1, scale: 10, tickSizeI64: 5},
		{value: "0.25", decimals: 2, scale: 100, tickSizeI64: 25},
		{value: "2.000", decimals: 0, scale: 1, tickSizeI64: 2},
	}
	for _, test := range tests {
		decimals, scale, tickSizeI64, err := fixedPriceParameters(test.value)
		if err != nil {
			t.Fatalf("tick %s: %v", test.value, err)
		}
		if decimals != test.decimals || scale != test.scale || tickSizeI64 != test.tickSizeI64 {
			t.Fatalf("tick %s = (%d, %d, %d), want (%d, %d, %d)", test.value, decimals, scale, tickSizeI64, test.decimals, test.scale, test.tickSizeI64)
		}
	}
}

func newAutomaticConfigService(t *testing.T, fixture string) (*Service, *storage.PathGuard, string) {
	return newAutomaticConfigServiceWithName(t, "28#SR701.txt", fixture)
}

func newAutomaticConfigServiceWithName(t *testing.T, sourceName, fixture string) (*Service, *storage.PathGuard, string) {
	t.Helper()
	root := t.TempDir()
	guard, err := storage.NewPathGuard(root)
	if err != nil {
		t.Fatal(err)
	}
	historyDir := filepath.Join(root, "history")
	if err := os.MkdirAll(historyDir, 0o750); err != nil {
		t.Fatal(err)
	}
	sourcePath := filepath.Join(historyDir, sourceName)
	if err := os.WriteFile(sourcePath, encodeFixture(t, fixture), 0o640); err != nil {
		t.Fatal(err)
	}
	store, err := catalog.NewStore(guard)
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	logger, _ := logx.New(logx.Options{Service: "test", Writer: &output})
	var cfg appconfig.Config
	cfg.Import.SourceDirectory = "history"
	cfg.Import.FailOnDuplicateTimestamp = true
	cfg.Import.KeepZeroVolumeBars = true
	cfg.Storage.ParquetRowGroupSize = 32768
	return NewService(guard, store, cfg, logger), guard, sourcePath
}

func futuresCalendarFixture() string {
	return `<html><body><table><tbody><tr>
<td>大商所</td><td>豆油</td><td>Y</td><td>13.0%</td><td>6.0%</td><td>10</td><td>1</td><td>1000</td><td></td><td></td>
</tr></tbody></table></body></html>`
}

func yL9Fixture() string {
	return `YL9 豆油加权 5分钟线 不复权
      日期      时间      开盘      最高      最低      收盘      成交量      持仓量      结算价
2026/01/02,1500,8010,8012,8008,8010,10,100,8010
2026/01/05,2105,8012,8016,8010,8014,12,101,8012
2026/01/05,2300,8014,8018,8012,8016,8,102,8014
2026/01/05,0905,8016,8020,8014,8018,15,103,8016
2026/01/05,1500,8018,8022,8016,8020,20,104,8018
`
}

func readRuntimeConfigFiles(t *testing.T, guard *storage.PathGuard) map[string][]byte {
	t.Helper()
	result := make(map[string][]byte)
	for _, name := range []string{"instruments.json", "sessions.json", "trading_calendar.csv"} {
		path, err := guard.Resolve("config/" + name)
		if err != nil {
			t.Fatal(err)
		}
		result[name], err = os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
	}
	return result
}

func sr701Fixture() string {
	return `SR701 白糖701 5分钟线 不复权
      日期      时间      开盘      最高      最低      收盘      成交量      持仓量      结算价
2026/01/02,1500,6010,6011,6009,6010,10,100,6010
2026/01/05,2105,6011,6013,6010,6012,12,101,6011
2026/01/05,2300,6012,6014,6011,6013,8,102,6012
2026/01/05,0905,6013,6015,6012,6014,15,103,6013
2026/01/05,1500,6014,6016,6013,6015,20,104,6014
`
}

func TestImportDoesNotApplyChartDisplayWindow(t *testing.T) {
	// 测试图表起止时间不会改变权威标准化数据；期望调整显示窗口后仍复用完整历史修订。
	service, _, _, _ := newTestService(t)
	items, err := service.Scan(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	request := ImportRequest{
		SourceFileID: items[0].SourceFileID, ImporterID: AdapterID, Exchange: "SHFE", Instrument: "AO2609", Timeframe: "5m",
		DateSemantics: "trading_day", Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end",
	}
	full, _, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil {
		t.Fatal(err)
	}
	service.config.App.Timezone = "Asia/Shanghai"
	service.config.Chart.BeginDT = "2025-09-22 01:00:00"
	service.config.Chart.EndDT = "2025-09-22 09:05:00"
	second, reused, err := service.Import(context.Background(), request, func(float64) {})
	if err != nil {
		t.Fatal(err)
	}
	if !reused || second.DataRevision != full.DataRevision {
		t.Fatalf("chart bounds changed import identity: reused=%v full=%s second=%s", reused, full.DataRevision, second.DataRevision)
	}
	if second.Coverage.BarCount != 5 || second.Coverage.FirstBarIndex != 0 || second.Coverage.LastBarIndex != 4 {
		t.Fatalf("chart bounds truncated authoritative data: %#v", second)
	}
}

func newTestService(t *testing.T) (*Service, *storage.PathGuard, *catalog.Store, string) {
	t.Helper()
	root := t.TempDir()
	guard, err := storage.NewPathGuard(root)
	if err != nil {
		t.Fatal(err)
	}
	configDir := filepath.Join(root, "config")
	historyDir := filepath.Join(root, "history")
	if err := os.MkdirAll(configDir, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(historyDir, 0o750); err != nil {
		t.Fatal(err)
	}
	instruments := `{"schema_version":1,"instruments":[{"exchange":"SHFE","product":"AO","symbol_pattern":"^AO[0-9]{4}$","display_name":"氧化铝","timezone":"Asia/Shanghai","price_decimals":0,"price_scale":1,"tick_size_i64":1,"contract_multiplier":20,"session_template_id":"test"}]}`
	sessions := `{"schema_version":1,"templates":[{"id":"test","timezone":"Asia/Shanghai","night_start":"21:00","night_end":"01:00","segments":[{"name":"night_before","start":"21:00","end":"24:00","calendar_date_rule":"night_session_date"},{"name":"night_after","start":"00:00","end":"01:00","calendar_date_rule":"trading_day"},{"name":"day","start":"09:00","end":"15:00","calendar_date_rule":"trading_day"}]}]}`
	calendar := "trading_day,night_session_date,is_open,note\n2025-09-22,2025-09-19,true,test\n"
	for name, data := range map[string]string{"instruments.json": instruments, "sessions.json": sessions, "trading_calendar.csv": calendar} {
		if err := os.WriteFile(filepath.Join(configDir, name), []byte(data), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	sourcePath := filepath.Join(historyDir, "30#AO2609.txt")
	if err := os.WriteFile(sourcePath, encodeFixture(t, validFixture()), 0o640); err != nil {
		t.Fatal(err)
	}
	store, err := catalog.NewStore(guard)
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	logger, _ := logx.New(logx.Options{Service: "test", Writer: &output})
	var cfg appconfig.Config
	cfg.Import.SourceDirectory = "history"
	cfg.Import.FailOnDuplicateTimestamp = true
	cfg.Import.KeepZeroVolumeBars = true
	cfg.Storage.ParquetRowGroupSize = 32768
	return NewService(guard, store, cfg, logger), guard, store, sourcePath
}

func testRuntimeParts(t *testing.T) (InstrumentConfig, SessionConfig, map[string]CalendarEntry) {
	t.Helper()
	service, guard, _, _ := newTestService(t)
	_ = service
	config, err := loadRuntimeConfig(guard)
	if err != nil {
		t.Fatal(err)
	}
	instrument, _ := config.instrument("SHFE", "AO2609")
	return instrument, config.sessions["test"], config.calendar
}

func encodeFixture(t *testing.T, value string) []byte {
	t.Helper()
	encoded, err := simplifiedchinese.GB18030.NewEncoder().Bytes([]byte(strings.ReplaceAll(value, "\n", "\r\n")))
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func validFixture() string {
	return "AO2609 氧化铝2609 5分钟线 不复权\n" +
		"      日期\t    时间\t    开盘\t    最高\t    最低\t    收盘\t    成交量\t    持仓量\t    结算价\n" +
		"2025/09/22,2145,3108,3110,3107,3109,1,1,0\n" +
		"2025/09/22,2305,3110,3112,3109,3111,2,3,0\n" +
		"2025/09/22,0100,3112,3113,3111,3112,3,6,0\n" +
		"2025/09/22,0905,3112,3112,3110,3111,0,6,0\n" +
		"2025/09/22,1500,3111,3114,3110,3113,8,10,3112\n" +
		"#数据来源:通达信\n"
}

func TestMetadataMarshalsToExpectedShape(t *testing.T) {
	// 测试导入元数据的 JSON 结构；期望包含数据集身份、定点价格配置和完整覆盖范围。
	service, _, _, _ := newTestService(t)
	items, _ := service.Scan(context.Background())
	meta, _, err := service.Import(context.Background(), ImportRequest{
		SourceFileID: items[0].SourceFileID, ImporterID: AdapterID, Exchange: "SHFE", Instrument: "AO2609", Timeframe: "5m",
		DateSemantics: "trading_day", Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end",
	}, func(float64) {})
	if err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(meta)
	if err != nil || !json.Valid(data) || !strings.Contains(string(data), `"data_revision":"sha256:`) {
		t.Fatalf("invalid metadata JSON: %s / %v", data, err)
	}
}

func TestDataRevisionChangesForEverySemanticInput(t *testing.T) {
	// 测试所有语义输入对数据修订哈希的影响；期望任一来源、配置或导入器版本变化都会改变修订。
	baseConfig := runtimeConfig{
		instrumentHash: "sha256:instrument", sessionHash: "sha256:session", calendarHash: "sha256:calendar",
	}
	base := dataRevision([]byte("source"), []byte(`{"mode":"strict"}`), baseConfig, AdapterID, AdapterVersion)
	cases := []struct {
		name    string
		source  []byte
		options []byte
		config  runtimeConfig
		version string
	}{
		{"source", []byte("sourcf"), []byte(`{"mode":"strict"}`), baseConfig, AdapterVersion},
		{"options", []byte("source"), []byte(`{"mode":"lenient"}`), baseConfig, AdapterVersion},
		{"time_window", []byte("source"), []byte(`{"begin_timestamp_utc":1700000000000,"mode":"strict"}`), baseConfig, AdapterVersion},
		{"instrument", []byte("source"), []byte(`{"mode":"strict"}`), runtimeConfig{instrumentHash: "changed", sessionHash: baseConfig.sessionHash, calendarHash: baseConfig.calendarHash}, AdapterVersion},
		{"session", []byte("source"), []byte(`{"mode":"strict"}`), runtimeConfig{instrumentHash: baseConfig.instrumentHash, sessionHash: "changed", calendarHash: baseConfig.calendarHash}, AdapterVersion},
		{"calendar", []byte("source"), []byte(`{"mode":"strict"}`), runtimeConfig{instrumentHash: baseConfig.instrumentHash, sessionHash: baseConfig.sessionHash, calendarHash: "changed"}, AdapterVersion},
		{"importer", []byte("source"), []byte(`{"mode":"strict"}`), baseConfig, "2.0.0"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := dataRevision(test.source, test.options, test.config, AdapterID, test.version); got == base {
				t.Fatalf("revision did not change for %s", test.name)
			}
		})
	}
}

func TestDuplicateTimestampIsNeverSilentlyOverwritten(t *testing.T) {
	// 测试重复时间戳处理；期望严格模式报告错误，非严格模式也必须显式记录去重事实。
	fixture := strings.Replace(validFixture(),
		"2025/09/22,2305,3110,3112,3109,3111,2,3,0",
		"2025/09/22,2145,3110,3112,3109,3111,2,3,0", 1)
	data := encodeFixture(t, fixture)
	instrument, session, calendar := testRuntimeParts(t)
	result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai",
		FailOnDuplicate: true, KeepZeroVolumeBars: true,
	})
	if err == nil || result.Quality.Counts["duplicate_count"] != 1 {
		t.Fatalf("duplicate was not rejected: err=%v quality=%#v", err, result.Quality)
	}
}

func TestTimestampSemanticsUseDifferentSessionBoundaries(t *testing.T) {
	// 测试起始时间与结束时间两种 K 线时间语义；期望时段边界归属按各自规则判断。
	session := SessionConfig{Segments: []SessionSegment{{Name: "day", Start: "09:00", End: "15:00"}}}
	if got := segmentAt(session, 900, "bar_start"); got != "day" {
		t.Fatalf("09:00 bar_start segment = %q", got)
	}
	if got := segmentAt(session, 900, "bar_end"); got != "" {
		t.Fatalf("09:00 bar_end segment = %q", got)
	}
	if got := segmentAt(session, 1500, "bar_start"); got != "" {
		t.Fatalf("15:00 bar_start segment = %q", got)
	}
	if got := segmentAt(session, 1500, "bar_end"); got != "day" {
		t.Fatalf("15:00 bar_end segment = %q", got)
	}
	if !sessionComplete(session, 1455, "bar_start", 5) || !sessionComplete(session, 1500, "bar_end", 5) {
		t.Fatal("complete final bars were classified as incomplete")
	}
	if sessionComplete(session, 1450, "bar_start", 5) || sessionComplete(session, 1455, "bar_end", 5) {
		t.Fatal("incomplete final bars were classified as complete")
	}
}

func TestGapIsFlaggedWithoutSynthesizingBars(t *testing.T) {
	// 测试交易时段内缺失 K 线；期望标记缺口但不自动生成任何补齐 K 线。
	fixture := strings.Replace(validFixture(),
		"2025/09/22,2305,3110,3112,3109,3111,2,3,0",
		"2025/09/22,2355,3110,3112,3109,3111,2,3,0", 1)
	data := encodeFixture(t, fixture)
	instrument, session, calendar := testRuntimeParts(t)
	result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai",
		FailOnDuplicate: true, KeepZeroVolumeBars: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Bars) != 5 || result.Quality.Counts["gap_count"] < 1 || result.Bars[1].Flags&FlagSessionGapBefore == 0 {
		t.Fatalf("gap result = bars:%d quality:%#v flags:%d", len(result.Bars), result.Quality.Counts, result.Bars[1].Flags)
	}
}

func TestNegativeVolumeAndOpenInterestAreRejected(t *testing.T) {
	// 测试负成交量与负持仓量；期望两类非法数量字段都被质量检查拒绝并保留来源行号。
	tests := []struct {
		name         string
		oldValue     string
		newValue     string
		expectedCode string
	}{
		{"volume", "3109,1,1,0", "3109,-1,1,0", "INVALID_VOLUME"},
		{"open_interest", "3109,1,1,0", "3109,1,-1,0", "INVALID_OPEN_INTEREST"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			data := encodeFixture(t, strings.Replace(validFixture(), test.oldValue, test.newValue, 1))
			instrument, session, calendar := testRuntimeParts(t)
			result, err := ParseTdx(data, "history/sample.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
				DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai", KeepZeroVolumeBars: true,
			})
			if err == nil || result.Quality.Counts["error_count"] != 1 || len(result.Quality.Issues) == 0 || result.Quality.Issues[0].Code != test.expectedCode || result.Quality.Issues[0].SourceLine != 3 {
				t.Fatalf("negative %s result: err=%v quality=%#v", test.name, err, result.Quality)
			}
		})
	}
}
