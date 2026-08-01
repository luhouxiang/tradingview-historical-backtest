package importer

import (
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type instrumentFile struct {
	SchemaVersion int                `json:"schema_version"`
	Instruments   []InstrumentConfig `json:"instruments"`
}

type InstrumentConfig struct {
	Exchange           string `json:"exchange"`
	Product            string `json:"product"`
	SymbolPattern      string `json:"symbol_pattern"`
	DisplayName        string `json:"display_name"`
	Timezone           string `json:"timezone"`
	PriceDecimals      int    `json:"price_decimals"`
	PriceScale         int64  `json:"price_scale"`
	TickSizeI64        int64  `json:"tick_size_i64"`
	ContractMultiplier int64  `json:"contract_multiplier"`
	SessionTemplateID  string `json:"session_template_id"`
	pattern            *regexp.Regexp
}

type sessionFile struct {
	SchemaVersion int             `json:"schema_version"`
	Templates     []SessionConfig `json:"templates"`
}

type SessionConfig struct {
	ID         string           `json:"id"`
	Timezone   string           `json:"timezone"`
	NightStart string           `json:"night_start"`
	NightEnd   string           `json:"night_end"`
	Segments   []SessionSegment `json:"segments"`
	nightHHMM  int
}

type SessionSegment struct {
	Name             string `json:"name"`
	Start            string `json:"start"`
	End              string `json:"end"`
	CalendarDateRule string `json:"calendar_date_rule"`
}

type CalendarEntry struct {
	TradingDay       string
	NightSessionDate string
	IsOpen           bool
}

type runtimeConfig struct {
	instruments    []InstrumentConfig
	sessions       map[string]SessionConfig
	calendar       map[string]CalendarEntry
	instrumentHash string
	sessionHash    string
	calendarHash   string
}

func loadRuntimeConfig(guard *storage.PathGuard) (runtimeConfig, error) {
	instrumentPath, err := guard.Resolve("config/instruments.json")
	if err != nil {
		return runtimeConfig{}, err
	}
	sessionPath, err := guard.Resolve("config/sessions.json")
	if err != nil {
		return runtimeConfig{}, err
	}
	calendarPath, err := guard.Resolve("config/trading_calendar.csv")
	if err != nil {
		return runtimeConfig{}, err
	}
	instrumentBytes, err := os.ReadFile(instrumentPath)
	if err != nil {
		return runtimeConfig{}, fmt.Errorf("read instruments config: %w", err)
	}
	sessionBytes, err := os.ReadFile(sessionPath)
	if err != nil {
		return runtimeConfig{}, fmt.Errorf("read sessions config: %w", err)
	}
	calendarBytes, err := os.ReadFile(calendarPath)
	if err != nil {
		return runtimeConfig{}, fmt.Errorf("read trading calendar: %w", err)
	}
	var instruments instrumentFile
	if err := json.Unmarshal(instrumentBytes, &instruments); err != nil {
		return runtimeConfig{}, fmt.Errorf("decode instruments config: %w", err)
	}
	if instruments.SchemaVersion != 1 {
		return runtimeConfig{}, fmt.Errorf("unsupported instruments schema_version %d", instruments.SchemaVersion)
	}
	for index := range instruments.Instruments {
		pattern, err := regexp.Compile(instruments.Instruments[index].SymbolPattern)
		if err != nil {
			return runtimeConfig{}, fmt.Errorf("compile instrument symbol pattern: %w", err)
		}
		instruments.Instruments[index].pattern = pattern
	}
	var sessions sessionFile
	if err := json.Unmarshal(sessionBytes, &sessions); err != nil {
		return runtimeConfig{}, fmt.Errorf("decode sessions config: %w", err)
	}
	if sessions.SchemaVersion != 1 {
		return runtimeConfig{}, fmt.Errorf("unsupported sessions schema_version %d", sessions.SchemaVersion)
	}
	sessionByID := make(map[string]SessionConfig, len(sessions.Templates))
	for _, session := range sessions.Templates {
		hhmm := 2400
		if session.NightStart != "" {
			var err error
			hhmm, err = parseClock(session.NightStart)
			if err != nil {
				return runtimeConfig{}, fmt.Errorf("session %s night_start: %w", session.ID, err)
			}
		}
		session.nightHHMM = hhmm
		sessionByID[session.ID] = session
	}
	calendar, err := parseCalendar(calendarBytes)
	if err != nil {
		return runtimeConfig{}, err
	}
	return runtimeConfig{
		instruments:    instruments.Instruments,
		sessions:       sessionByID,
		calendar:       calendar,
		instrumentHash: hashBytes(instrumentBytes),
		sessionHash:    hashBytes(sessionBytes),
		calendarHash:   hashBytes(calendarBytes),
	}, nil
}

func (c runtimeConfig) instrument(exchange, symbol string) (InstrumentConfig, bool) {
	for _, instrument := range c.instruments {
		if instrument.Exchange == exchange && instrument.pattern.MatchString(symbol) {
			return instrument, true
		}
	}
	return InstrumentConfig{}, false
}

func parseCalendar(data []byte) (map[string]CalendarEntry, error) {
	reader := csv.NewReader(strings.NewReader(strings.TrimPrefix(string(data), "\ufeff")))
	records, err := reader.ReadAll()
	if err != nil {
		return nil, fmt.Errorf("decode trading calendar: %w", err)
	}
	if len(records) < 2 || len(records[0]) < 3 || records[0][0] != "trading_day" || records[0][1] != "night_session_date" {
		return nil, fmt.Errorf("invalid trading calendar header")
	}
	entries := make(map[string]CalendarEntry, len(records)-1)
	for row, record := range records[1:] {
		if len(record) < 3 {
			return nil, fmt.Errorf("trading calendar row %d has too few fields", row+2)
		}
		if _, err := time.Parse(time.DateOnly, record[0]); err != nil {
			return nil, fmt.Errorf("trading calendar row %d trading_day: %w", row+2, err)
		}
		if _, err := time.Parse(time.DateOnly, record[1]); err != nil {
			return nil, fmt.Errorf("trading calendar row %d night_session_date: %w", row+2, err)
		}
		isOpen, err := strconv.ParseBool(record[2])
		if err != nil {
			return nil, fmt.Errorf("trading calendar row %d is_open: %w", row+2, err)
		}
		entries[record[0]] = CalendarEntry{record[0], record[1], isOpen}
	}
	return entries, nil
}

func parseClock(value string) (int, error) {
	parts := strings.Split(value, ":")
	if len(parts) != 2 {
		return 0, fmt.Errorf("invalid clock %q", value)
	}
	hour, err := strconv.Atoi(parts[0])
	if err != nil || hour < 0 || hour > 24 {
		return 0, fmt.Errorf("invalid clock %q", value)
	}
	minute, err := strconv.Atoi(parts[1])
	if err != nil || minute < 0 || minute > 59 || hour == 24 && minute != 0 {
		return 0, fmt.Errorf("invalid clock %q", value)
	}
	return hour*100 + minute, nil
}

func hashBytes(data []byte) string {
	hash := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(hash[:])
}
