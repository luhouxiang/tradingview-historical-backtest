package importer

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"golang.org/x/text/encoding/simplifiedchinese"
)

const (
	AdapterID      = "tdx_txt_v1"
	AdapterVersion = "1.0.0"

	FlagZeroVolume           uint32 = 1 << 0
	FlagSessionGapBefore     uint32 = 1 << 1
	FlagIncompleteTradingDay uint32 = 1 << 2
	FlagSourceFieldMissing   uint32 = 1 << 3
	FlagManuallyCorrected    uint32 = 1 << 4
	FlagDuplicateResolved    uint32 = 1 << 5
)

var titlePattern = regexp.MustCompile(`^([A-Za-z]+[0-9]{4})\s+(.+?)\s+([0-9]+)分钟线\s+不复权$`)

type Detection struct {
	Symbol      string `json:"symbol"`
	DisplayName string `json:"display_name"`
	Timeframe   string `json:"timeframe"`
	Title       string `json:"title"`
	Encoding    string `json:"encoding"`
	Format      string `json:"format"`
}

type ImportOptions struct {
	DateSemantics      string `json:"date_semantics"`
	TimestampSemantics string `json:"timestamp_semantics"`
	Timezone           string `json:"timezone"`
	FailOnDuplicate    bool   `json:"fail_on_duplicate_timestamp"`
	KeepZeroVolumeBars bool   `json:"keep_zero_volume_bars"`
	FillMissingBars    bool   `json:"fill_missing_bars"`
}

type Bar struct {
	BarIndex      int64  `parquet:"bar_index"`
	TimestampUTC  int64  `parquet:"timestamp_utc"`
	TradingDay    int32  `parquet:"trading_day,date"`
	SourceHHMM    int32  `parquet:"source_hhmm"`
	OpenI64       int64  `parquet:"open_i64"`
	HighI64       int64  `parquet:"high_i64"`
	LowI64        int64  `parquet:"low_i64"`
	CloseI64      int64  `parquet:"close_i64"`
	Volume        int64  `parquet:"volume"`
	OpenInterest  *int64 `parquet:"open_interest,optional"`
	SettlementI64 *int64 `parquet:"settlement_i64,optional"`
	SourceLine    int64  `parquet:"source_line"`
	Flags         uint32 `parquet:"flags"`
}

type QualityIssue struct {
	Level      string `json:"level"`
	Code       string `json:"code"`
	Message    string `json:"message"`
	SourceLine int64  `json:"source_line,omitempty"`
}

type QualityReport struct {
	SchemaVersion int            `json:"schema_version"`
	SourcePath    string         `json:"source_path"`
	SourceSHA256  string         `json:"source_sha256"`
	Counts        map[string]int `json:"counts"`
	Issues        []QualityIssue `json:"issues"`
}

type ParseResult struct {
	Detection Detection
	Bars      []Bar
	Quality   QualityReport
}

type QualityError struct {
	Report QualityReport
}

func (e *QualityError) Error() string {
	return fmt.Sprintf("source data has %d quality errors", e.Report.Counts["error_count"])
}

func DetectTdx(data []byte) (Detection, error) {
	decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(data)
	if err != nil {
		return Detection{}, fmt.Errorf("decode GB18030: %w", err)
	}
	lines := splitLines(decoded)
	if len(lines) < 3 {
		return Detection{}, errors.New("tdx file has too few lines")
	}
	match := titlePattern.FindStringSubmatch(strings.TrimSpace(lines[0]))
	if match == nil {
		return Detection{}, errors.New("tdx title is not recognized")
	}
	if !strings.Contains(lines[1], "日期") || !strings.Contains(lines[1], "开盘") || !strings.Contains(lines[1], "结算价") {
		return Detection{}, errors.New("tdx header is not recognized")
	}
	minutes, err := strconv.Atoi(match[3])
	if err != nil || minutes < 1 {
		return Detection{}, errors.New("tdx timeframe is invalid")
	}
	return Detection{
		Symbol: strings.ToUpper(match[1]), DisplayName: strings.TrimSpace(match[2]),
		Timeframe: strconv.Itoa(minutes) + "m", Title: strings.TrimSpace(lines[0]),
		Encoding: "GB18030", Format: AdapterID,
	}, nil
}

func ParseTdx(data []byte, sourcePath, sourceHash string, instrument InstrumentConfig, session SessionConfig, calendar map[string]CalendarEntry, options ImportOptions) (ParseResult, error) {
	detection, err := DetectTdx(data)
	if err != nil {
		return ParseResult{}, err
	}
	decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(data)
	if err != nil {
		return ParseResult{}, err
	}
	location, err := time.LoadLocation(options.Timezone)
	if err != nil {
		return ParseResult{}, fmt.Errorf("load timezone: %w", err)
	}
	timeframeMinutes, _ := strconv.Atoi(strings.TrimSuffix(detection.Timeframe, "m"))
	report := QualityReport{
		SchemaVersion: 1, SourcePath: sourcePath, SourceSHA256: sourceHash,
		Counts: map[string]int{"duplicate_count": 0, "invalid_ohlc_count": 0, "zero_volume_count": 0, "gap_count": 0, "warning_count": 0, "error_count": 0},
		Issues: []QualityIssue{},
	}
	lines := splitLines(decoded)
	bars := make([]Bar, 0, len(lines)-3)
	seen := make(map[int64]int)
	var previousTimestamp int64 = -1
	previousSegment := ""
	for offset, line := range lines[2:] {
		sourceLine := int64(offset + 3)
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#数据来源:") {
			continue
		}
		fields := strings.Split(trimmed, ",")
		if len(fields) != 9 {
			addIssue(&report, "ERROR", "INVALID_FIELD_COUNT", "expected 9 comma-separated fields", sourceLine)
			continue
		}
		tradingDate, tradingDay, err := parseSourceDate(fields[0])
		if err != nil {
			addIssue(&report, "ERROR", "INVALID_DATE", err.Error(), sourceLine)
			continue
		}
		hhmm, err := parseHHMM(fields[1])
		if err != nil {
			addIssue(&report, "ERROR", "INVALID_TIME", err.Error(), sourceLine)
			continue
		}
		timestamp, err := mapTimestamp(tradingDate, tradingDay, hhmm, options.DateSemantics, session, calendar, location)
		if err != nil {
			addIssue(&report, "ERROR", "TRADING_CALENDAR_MAPPING_MISSING", err.Error(), sourceLine)
			continue
		}
		prices := make([]int64, 4)
		priceOK := true
		for index := range prices {
			prices[index], err = parseFixed(fields[index+2], instrument.PriceDecimals, instrument.PriceScale)
			if err != nil {
				addIssue(&report, "ERROR", "INVALID_PRICE", err.Error(), sourceLine)
				priceOK = false
				break
			}
		}
		if !priceOK {
			continue
		}
		volume, err := strconv.ParseInt(strings.TrimSpace(fields[6]), 10, 64)
		if err != nil || volume < 0 {
			addIssue(&report, "ERROR", "INVALID_VOLUME", "volume must be a non-negative integer", sourceLine)
			continue
		}
		openInterest, err := strconv.ParseInt(strings.TrimSpace(fields[7]), 10, 64)
		if err != nil || openInterest < 0 {
			addIssue(&report, "ERROR", "INVALID_OPEN_INTEREST", "open_interest must be a non-negative integer", sourceLine)
			continue
		}
		if prices[1] < prices[0] || prices[1] < prices[2] || prices[1] < prices[3] || prices[2] > prices[0] || prices[2] > prices[1] || prices[2] > prices[3] {
			report.Counts["invalid_ohlc_count"]++
			addIssue(&report, "ERROR", "INVALID_OHLC", "OHLC ordering is invalid", sourceLine)
			continue
		}
		flags := uint32(0)
		if volume == 0 {
			flags |= FlagZeroVolume
			report.Counts["zero_volume_count"]++
			report.Counts["warning_count"]++
			if !options.KeepZeroVolumeBars {
				addIssue(&report, "ERROR", "ZERO_VOLUME_REMOVAL_FORBIDDEN", "zero-volume bars must be retained", sourceLine)
				continue
			}
		}
		var settlement *int64
		if strings.TrimSpace(fields[8]) == "0" {
			flags |= FlagSourceFieldMissing
		} else {
			value, parseErr := parseFixed(fields[8], instrument.PriceDecimals, instrument.PriceScale)
			if parseErr != nil {
				addIssue(&report, "ERROR", "INVALID_SETTLEMENT", parseErr.Error(), sourceLine)
				continue
			}
			settlement = &value
		}
		if existing, duplicate := seen[timestamp]; duplicate {
			report.Counts["duplicate_count"]++
			if options.FailOnDuplicate {
				addIssue(&report, "ERROR", "DUPLICATE_TIMESTAMP", fmt.Sprintf("timestamp duplicates source line %d", bars[existing].SourceLine), sourceLine)
				continue
			}
			bars[existing].Flags |= FlagDuplicateResolved
			report.Counts["warning_count"]++
			continue
		}
		segment := segmentAt(session, hhmm, options.TimestampSemantics)
		if previousTimestamp >= 0 {
			if timestamp <= previousTimestamp {
				addIssue(&report, "ERROR", "NON_INCREASING_TIMESTAMP", "mapped timestamps must be strictly increasing", sourceLine)
				continue
			}
			if segment != "" && segment == previousSegment && timestamp-previousTimestamp > int64(timeframeMinutes)*int64(time.Minute/time.Millisecond) {
				flags |= FlagSessionGapBefore
				report.Counts["gap_count"]++
				report.Counts["warning_count"]++
			}
		}
		bar := Bar{
			BarIndex: int64(len(bars)), TimestampUTC: timestamp, TradingDay: date32(tradingDate), SourceHHMM: int32(hhmm),
			OpenI64: prices[0], HighI64: prices[1], LowI64: prices[2], CloseI64: prices[3], Volume: volume,
			OpenInterest: &openInterest, SettlementI64: settlement, SourceLine: sourceLine, Flags: flags,
		}
		seen[timestamp] = len(bars)
		bars = append(bars, bar)
		previousTimestamp = timestamp
		previousSegment = segment
	}
	if options.FillMissingBars {
		addIssue(&report, "ERROR", "FILL_MISSING_BARS_FORBIDDEN", "missing bars must not be synthesized", 0)
	}
	if len(bars) > 0 {
		lastDay := bars[len(bars)-1].TradingDay
		lastHHMM := int(bars[len(bars)-1].SourceHHMM)
		if !sessionComplete(session, lastHHMM, options.TimestampSemantics, timeframeMinutes) {
			for index := len(bars) - 1; index >= 0 && bars[index].TradingDay == lastDay; index-- {
				bars[index].Flags |= FlagIncompleteTradingDay
			}
			report.Counts["warning_count"]++
			report.Issues = append(report.Issues, QualityIssue{Level: "WARN", Code: "INCOMPLETE_TRADING_DAY", Message: "tail trading day is incomplete"})
		}
	}
	if report.Counts["error_count"] > 0 {
		return ParseResult{Detection: detection, Bars: bars, Quality: report}, &QualityError{Report: report}
	}
	return ParseResult{Detection: detection, Bars: bars, Quality: report}, nil
}

func addIssue(report *QualityReport, level, code, message string, line int64) {
	report.Issues = append(report.Issues, QualityIssue{Level: level, Code: code, Message: message, SourceLine: line})
	if level == "ERROR" {
		report.Counts["error_count"]++
	} else {
		report.Counts["warning_count"]++
	}
}

func splitLines(data []byte) []string {
	normalized := bytes.ReplaceAll(data, []byte("\r\n"), []byte("\n"))
	return strings.Split(strings.TrimSuffix(string(normalized), "\n"), "\n")
}

func parseSourceDate(value string) (time.Time, string, error) {
	trimmed := strings.TrimSpace(value)
	for _, layout := range []string{"2006/01/02", time.DateOnly} {
		if date, err := time.Parse(layout, trimmed); err == nil {
			return date, date.Format(time.DateOnly), nil
		}
	}
	return time.Time{}, "", fmt.Errorf("invalid source date %q", value)
}

func parseHHMM(value string) (int, error) {
	trimmed := strings.TrimSpace(value)
	if len(trimmed) > 4 {
		return 0, fmt.Errorf("invalid HHmm %q", value)
	}
	hhmm, err := strconv.Atoi(trimmed)
	if err != nil || hhmm < 0 || hhmm > 2359 || hhmm%100 > 59 {
		return 0, fmt.Errorf("invalid HHmm %q", value)
	}
	return hhmm, nil
}

func mapTimestamp(sourceDate time.Time, tradingDay string, hhmm int, semantics string, session SessionConfig, calendar map[string]CalendarEntry, location *time.Location) (int64, error) {
	date := sourceDate
	if semantics == "trading_day" && hhmm >= session.nightHHMM {
		entry, ok := calendar[tradingDay]
		if !ok || !entry.IsOpen || entry.NightSessionDate == "" {
			return 0, fmt.Errorf("no night_session_date for trading day %s", tradingDay)
		}
		mapped, err := time.Parse(time.DateOnly, entry.NightSessionDate)
		if err != nil {
			return 0, err
		}
		date = mapped
	} else if semantics != "trading_day" && semantics != "calendar_date" {
		return 0, fmt.Errorf("unknown date_semantics %q", semantics)
	}
	local := time.Date(date.Year(), date.Month(), date.Day(), hhmm/100, hhmm%100, 0, 0, location)
	return local.UTC().UnixMilli(), nil
}

func parseFixed(value string, decimals int, scale int64) (int64, error) {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return 0, errors.New("price is empty")
	}
	sign := int64(1)
	if strings.HasPrefix(trimmed, "-") {
		sign = -1
		trimmed = strings.TrimPrefix(trimmed, "-")
	}
	parts := strings.Split(trimmed, ".")
	if len(parts) > 2 || parts[0] == "" {
		return 0, fmt.Errorf("invalid price %q", value)
	}
	whole, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid price %q", value)
	}
	fraction := ""
	if len(parts) == 2 {
		fraction = parts[1]
	}
	if len(fraction) > decimals {
		return 0, fmt.Errorf("price %q exceeds configured precision", value)
	}
	fraction += strings.Repeat("0", decimals-len(fraction))
	fractionValue := int64(0)
	if fraction != "" {
		fractionValue, err = strconv.ParseInt(fraction, 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid price %q", value)
		}
	}
	if scale < 1 || whole > (1<<63-1-fractionValue)/scale {
		return 0, fmt.Errorf("price %q overflows int64", value)
	}
	return sign * (whole*scale + fractionValue), nil
}

func segmentAt(session SessionConfig, hhmm int, semantics string) string {
	minutes := hhmm/100*60 + hhmm%100
	for _, segment := range session.Segments {
		startHHMM, startErr := parseClock(segment.Start)
		endHHMM, endErr := parseClock(segment.End)
		if startErr != nil || endErr != nil {
			continue
		}
		start := startHHMM/100*60 + startHHMM%100
		end := endHHMM/100*60 + endHHMM%100
		if semantics == "bar_start" && minutes >= start && minutes < end || semantics != "bar_start" && minutes > start && minutes <= end {
			return segment.Name
		}
	}
	return ""
}

func sessionComplete(session SessionConfig, lastHHMM int, semantics string, timeframeMinutes int) bool {
	if len(session.Segments) == 0 {
		return true
	}
	last := session.Segments[len(session.Segments)-1]
	end, err := parseClock(last.End)
	if err != nil {
		return false
	}
	lastMinutes := lastHHMM/100*60 + lastHHMM%100
	endMinutes := end/100*60 + end%100
	if semantics == "bar_start" {
		return lastMinutes >= endMinutes-timeframeMinutes
	}
	return lastMinutes >= endMinutes
}

func date32(date time.Time) int32 {
	epoch := time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC)
	return int32(date.UTC().Sub(epoch) / (24 * time.Hour))
}

func canonicalOptions(options ImportOptions) ([]byte, string, error) {
	data, err := json.Marshal(options)
	if err != nil {
		return nil, "", err
	}
	return data, hashBytes(data), nil
}
