package importer

import (
	"bytes"
	"context"
	"encoding/json"
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

func TestDetectFullSample(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "samples", "30#AO2609.txt"))
	if err != nil {
		t.Fatal(err)
	}
	detection, err := DetectTdx(data)
	if err != nil {
		t.Fatal(err)
	}
	if detection.Symbol != "AO2609" || detection.Timeframe != "5m" || detection.Encoding != "GB18030" {
		t.Fatalf("unexpected detection: %#v", detection)
	}
}

func TestFullSampleImportsToParquetWithinTarget(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "samples", "30#AO2609.txt"))
	if err != nil {
		t.Fatal(err)
	}
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
	calendar := calendarFromSample(t, data)
	started := time.Now()
	result, err := ParseTdx(data, "history/30#AO2609.txt", hashBytes(data), instrument, session, calendar, ImportOptions{
		DateSemantics: "trading_day", TimestampSemantics: "bar_end", Timezone: "Asia/Shanghai",
		FailOnDuplicate: true, KeepZeroVolumeBars: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Bars) != 17017 || result.Quality.Counts["zero_volume_count"] != 1224 {
		t.Fatalf("full sample counts: bars=%d quality=%#v", len(result.Bars), result.Quality.Counts)
	}
	path := filepath.Join(t.TempDir(), "bars.parquet")
	revision := hashBytes(data)
	if err := writeBars(path, result.Bars, "SHFE.AO2609.5m", revision, ImportRequest{Timezone: "Asia/Shanghai", TimestampSemantics: "bar_end"}, instrument, 32768); err != nil {
		t.Fatal(err)
	}
	elapsed := time.Since(started)
	t.Logf("17,017-bar parse and Parquet write: %s", elapsed)
	if elapsed > 5*time.Second {
		t.Fatalf("full sample import exceeded 5s target: %s", elapsed)
	}
}

func calendarFromSample(t *testing.T, data []byte) map[string]CalendarEntry {
	t.Helper()
	decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(data)
	if err != nil {
		t.Fatal(err)
	}
	days := make([]string, 0)
	seen := map[string]bool{}
	for _, line := range splitLines(decoded)[2:] {
		fields := strings.Split(line, ",")
		if len(fields) != 9 {
			continue
		}
		_, day, err := parseSourceDate(fields[0])
		if err == nil && !seen[day] {
			seen[day] = true
			days = append(days, day)
		}
	}
	calendar := make(map[string]CalendarEntry, len(days))
	previous := "2025-09-15"
	for _, day := range days {
		calendar[day] = CalendarEntry{TradingDay: day, NightSessionDate: previous, IsOpen: true}
		previous = day
	}
	return calendar
}

func TestParseMapsNightSessionThroughCalendar(t *testing.T) {
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
	if meta.Coverage.BarCount != 5 || meta.Quality.ZeroVolumeCount != 1 {
		t.Fatalf("unexpected metadata: %#v", meta)
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
