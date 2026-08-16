package importer

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
	"golang.org/x/text/encoding/simplifiedchinese"
)

const automaticRuleCheckedAt = "2026-08-16"

var automaticInstrumentRules = []InstrumentConfig{
	{
		Exchange: "CZCE", Product: "SR", SymbolPattern: `^SR(?:[0-9]{3,4}|L[0-9])$`, DisplayName: "白糖",
		Timezone: "Asia/Shanghai", PriceDecimals: 0, PriceScale: 1, TickSizeI64: 1, ContractMultiplier: 10,
		SessionTemplateID: "cn_futures_night_2300",
		RuleSourceURL:     "https://www.czce.com.cn/cn/uploadfile/2024/06/26/20240626165544343.pdf",
		RuleVersion:       "czce-sr-2024-06-26", RuleCheckedAt: automaticRuleCheckedAt,
	},
}

var automaticSessionRules = []SessionConfig{
	{
		ID: "cn_futures_night_2300", Timezone: "Asia/Shanghai", NightStart: "21:00", NightEnd: "23:00",
		Segments: []SessionSegment{
			{Name: "night", Start: "21:00", End: "23:00", CalendarDateRule: "night_session_date"},
			{Name: "day_1", Start: "09:00", End: "10:15", CalendarDateRule: "trading_day"},
			{Name: "day_2", Start: "10:30", End: "11:30", CalendarDateRule: "trading_day"},
			{Name: "day_3", Start: "13:30", End: "15:00", CalendarDateRule: "trading_day"},
		},
		RuleSourceURL: "https://www.czce.com.cn/cn/uploadfile/2024/06/26/20240626165544343.pdf",
		RuleVersion:   "czce-sr-2024-06-26", RuleCheckedAt: automaticRuleCheckedAt,
	},
}

var automaticCalendarSeedRules = map[string]CalendarEntry{
	"SHFE.AO.2023-06-20": {
		TradingDay: "2023-06-20", NightSessionDate: "2023-06-19", IsOpen: true,
		Note: "official:shfe-ao-listing-2023-06-19",
	},
}

type autoConfigSource struct {
	SourceFileID string
	Path         string
	Detection    Detection
	Data         []byte
}

// ensureAutoRuntimeConfig 只补充能够确定来源的配置，绝不覆盖已有条目。
func ensureAutoRuntimeConfig(ctx context.Context, guard *storage.PathGuard, sources []autoConfigSource, resolver instrumentResolver) (map[string]error, error) {
	resolutionErrors := make(map[string]error)
	instrumentPath, err := guard.Resolve("config/instruments.json")
	if err != nil {
		return nil, err
	}
	sessionPath, err := guard.Resolve("config/sessions.json")
	if err != nil {
		return nil, err
	}
	calendarPath, err := guard.Resolve("config/trading_calendar.csv")
	if err != nil {
		return nil, err
	}
	instrumentFileExists := fileExists(instrumentPath)
	sessionFileExists := fileExists(sessionPath)
	calendarFileExists := fileExists(calendarPath)
	instruments, err := readInstrumentFile(instrumentPath)
	if err != nil {
		return nil, err
	}
	sessions, err := readSessionFile(sessionPath)
	if err != nil {
		return nil, err
	}
	instrumentChanged := false
	sessionChanged := false
	for _, source := range sources {
		exchangeHint := exchangeFromTDXPath(source.Path)
		if !instrumentFileMatches(instruments, exchangeHint, source.Detection.Symbol) {
			rule, ok := automaticInstrumentRule(source.Detection.Symbol)
			if !ok {
				if resolver == nil {
					resolutionErrors[source.SourceFileID] = fmt.Errorf("没有可用的联网品种配置解析器")
					continue
				}
				var session SessionConfig
				rule, session, err = resolver.Resolve(ctx, source)
				if err != nil {
					resolutionErrors[source.SourceFileID] = err
					continue
				}
				if !sessionFileContains(sessions, session.ID) {
					sessions.Templates = append(sessions.Templates, session)
					sessionChanged = true
				}
			}
			instruments.Instruments = append(instruments.Instruments, rule)
			instrumentChanged = true
		}
		instrument, found := instrumentFromFileForExchange(instruments, exchangeHint, source.Detection.Symbol)
		if found && !sessionFileContains(sessions, instrument.SessionTemplateID) {
			session, found := automaticSessionRule(instrument.SessionTemplateID)
			if !found {
				continue
			}
			sessions.Templates = append(sessions.Templates, session)
			sessionChanged = true
		}
	}
	// 同品种的后续源文件若已成功生成配置，前面一次临时联网失败不应继续阻塞该品种。
	for _, source := range sources {
		if instrumentFileMatches(instruments, exchangeFromTDXPath(source.Path), source.Detection.Symbol) {
			delete(resolutionErrors, source.SourceFileID)
		}
	}
	calendar, err := readCalendarFile(calendarPath)
	if err != nil {
		return nil, err
	}
	calendarChanged, err := supplementCalendar(ctx, calendar, sources, instruments, sessions, resolver)
	if err != nil {
		return nil, err
	}
	if instrumentChanged || !instrumentFileExists {
		if err := writeJSONFile(instrumentPath, instruments); err != nil {
			return nil, err
		}
	}
	if sessionChanged || !sessionFileExists {
		if err := writeJSONFile(sessionPath, sessions); err != nil {
			return nil, err
		}
	}
	if calendarChanged || !calendarFileExists {
		if err := writeCalendarFile(calendarPath, calendar); err != nil {
			return nil, err
		}
	}
	return resolutionErrors, nil
}

func readInstrumentFile(path string) (instrumentFile, error) {
	var document instrumentFile
	if err := readOptionalJSON(path, &document); err != nil {
		return instrumentFile{}, fmt.Errorf("decode instruments config: %w", err)
	}
	if document.SchemaVersion == 0 {
		document.SchemaVersion = 1
	}
	if document.SchemaVersion != 1 {
		return instrumentFile{}, fmt.Errorf("unsupported instruments schema_version %d", document.SchemaVersion)
	}
	return document, nil
}

func readSessionFile(path string) (sessionFile, error) {
	var document sessionFile
	if err := readOptionalJSON(path, &document); err != nil {
		return sessionFile{}, fmt.Errorf("decode sessions config: %w", err)
	}
	if document.SchemaVersion == 0 {
		document.SchemaVersion = 1
	}
	if document.SchemaVersion != 1 {
		return sessionFile{}, fmt.Errorf("unsupported sessions schema_version %d", document.SchemaVersion)
	}
	return document, nil
}

func readOptionalJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}

func readCalendarFile(path string) (map[string]CalendarEntry, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return make(map[string]CalendarEntry), nil
	}
	if err != nil {
		return nil, err
	}
	return parseCalendar(data)
}

func automaticInstrumentRule(symbol string) (InstrumentConfig, bool) {
	for _, rule := range automaticInstrumentRules {
		if regexp.MustCompile(rule.SymbolPattern).MatchString(symbol) {
			return rule, true
		}
	}
	return InstrumentConfig{}, false
}

func automaticSessionRule(id string) (SessionConfig, bool) {
	for _, rule := range automaticSessionRules {
		if rule.ID == id {
			return rule, true
		}
	}
	return SessionConfig{}, false
}

func instrumentFileMatches(document instrumentFile, exchange, symbol string) bool {
	for _, instrument := range document.Instruments {
		if (exchange == "" || instrument.Exchange == exchange) && instrumentMatchesSymbol(instrument, symbol) {
			return true
		}
	}
	return false
}

func sessionFileContains(document sessionFile, id string) bool {
	for _, session := range document.Templates {
		if session.ID == id {
			return true
		}
	}
	return false
}

func supplementCalendar(ctx context.Context, calendar map[string]CalendarEntry, sources []autoConfigSource, instruments instrumentFile, sessions sessionFile, resolver instrumentResolver) (bool, error) {
	changed := false
	knownDays := make(map[string]bool, len(calendar))
	for day, entry := range calendar {
		if entry.IsOpen {
			knownDays[day] = true
		}
	}
	neededDays := make(map[string]bool)
	for _, source := range sources {
		instrument, ok := instrumentFromFile(instruments, source.Detection.Symbol)
		if !ok {
			continue
		}
		session, ok := sessionFromFile(sessions, instrument.SessionTemplateID)
		if !ok {
			continue
		}
		observations, err := sourceTradingDays(source.Data, session)
		if err != nil {
			return false, err
		}
		for day, hasNight := range observations {
			knownDays[day] = true
			if hasNight {
				neededDays[day] = true
				seedKey := instrument.Exchange + "." + instrument.Product + "." + day
				if _, exists := calendar[day]; !exists {
					if seed, found := automaticCalendarSeedRules[seedKey]; found {
						calendar[day] = seed
						knownDays[seed.NightSessionDate] = true
						changed = true
					}
				}
			}
		}
	}
	days := make([]string, 0, len(knownDays))
	for day := range knownDays {
		days = append(days, day)
	}
	sort.Strings(days)
	for day := range neededDays {
		if entry, exists := calendar[day]; exists && entry.IsOpen && entry.NightSessionDate != "" {
			continue
		}
		position := sort.SearchStrings(days, day)
		if position >= len(days) || days[position] != day {
			continue
		}
		if position == 0 {
			if resolver == nil {
				continue
			}
			previousDay, _, resolveErr := resolver.PreviousTradingDay(ctx, day)
			if resolveErr != nil {
				if ctx.Err() != nil {
					return false, ctx.Err()
				}
				continue
			}
			calendar[day] = CalendarEntry{TradingDay: day, NightSessionDate: previousDay, IsOpen: true, Note: "online:eastmoney-cn-trading-day"}
			changed = true
			continue
		}
		calendar[day] = CalendarEntry{TradingDay: day, NightSessionDate: days[position-1], IsOpen: true, Note: "auto:tdx-observed-trading-days"}
		changed = true
	}
	return changed, nil
}

func instrumentFromFile(document instrumentFile, symbol string) (InstrumentConfig, bool) {
	return instrumentFromFileForExchange(document, "", symbol)
}

func instrumentFromFileForExchange(document instrumentFile, exchange, symbol string) (InstrumentConfig, bool) {
	for _, instrument := range document.Instruments {
		if (exchange == "" || instrument.Exchange == exchange) && instrumentMatchesSymbol(instrument, symbol) {
			return instrument, true
		}
	}
	return InstrumentConfig{}, false
}

func sessionFromFile(document sessionFile, id string) (SessionConfig, bool) {
	for _, session := range document.Templates {
		if session.ID == id {
			if session.NightStart == "" {
				session.nightHHMM = 2400
			} else if hhmm, err := parseClock(session.NightStart); err == nil {
				session.nightHHMM = hhmm
			}
			return session, true
		}
	}
	return SessionConfig{}, false
}

func sourceTradingDays(data []byte, session SessionConfig) (map[string]bool, error) {
	decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(data)
	if err != nil {
		return nil, fmt.Errorf("decode GB18030 while generating calendar: %w", err)
	}
	observations := make(map[string]bool)
	for _, line := range splitLines(decoded)[2:] {
		fields := strings.Split(strings.TrimSpace(line), ",")
		if len(fields) != 9 {
			continue
		}
		_, day, dateErr := parseSourceDate(fields[0])
		hhmm, timeErr := parseHHMM(fields[1])
		if dateErr != nil || timeErr != nil {
			continue
		}
		if _, exists := observations[day]; !exists {
			observations[day] = false
		}
		if hhmm >= session.nightHHMM {
			observations[day] = true
		}
	}
	return observations, nil
}

func missingCalendarMapping(data []byte, session SessionConfig, calendar map[string]CalendarEntry) (string, bool) {
	observations, err := sourceTradingDays(data, session)
	if err != nil {
		return "", true
	}
	for day, hasNight := range observations {
		entry, exists := calendar[day]
		if hasNight && (!exists || !entry.IsOpen || entry.NightSessionDate == "") {
			return day, true
		}
	}
	return "", false
}

func writeJSONFile(path string, document any) error {
	data, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return err
	}
	return storage.AtomicWriteFile(path, append(data, '\n'), 0o640)
}

func writeCalendarFile(path string, calendar map[string]CalendarEntry) error {
	var buffer bytes.Buffer
	writer := csv.NewWriter(&buffer)
	if err := writer.Write([]string{"trading_day", "night_session_date", "is_open", "note"}); err != nil {
		return err
	}
	days := make([]string, 0, len(calendar))
	for day := range calendar {
		days = append(days, day)
	}
	sort.Strings(days)
	for _, day := range days {
		entry := calendar[day]
		if err := writer.Write([]string{entry.TradingDay, entry.NightSessionDate, strconv.FormatBool(entry.IsOpen), entry.Note}); err != nil {
			return err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		return err
	}
	return storage.AtomicWriteFile(path, buffer.Bytes(), 0o640)
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
