package backtest

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"

	"github.com/parquet-go/parquet-go"
)

type Trade struct {
	TradeID                    string `json:"trade_id" parquet:"trade_id"`
	Side                       string `json:"side" parquet:"side"`
	EntryBarIndex              int64  `json:"entry_bar_index" parquet:"entry_bar_index"`
	EntryTime                  int64  `json:"entry_time" parquet:"entry_time"`
	EntryPriceI64              int64  `json:"entry_price_i64" parquet:"entry_price_i64"`
	EntrySignalID              string `json:"entry_signal_id" parquet:"entry_signal_id,optional"`
	EntrySignalKnownAtBarIndex int64  `json:"entry_signal_known_at_bar_index" parquet:"entry_signal_known_at_bar_index,optional"`
	EntryOrderID               string `json:"entry_order_id" parquet:"entry_order_id,optional"`
	ExitBarIndex               int64  `json:"exit_bar_index" parquet:"exit_bar_index"`
	ExitTime                   int64  `json:"exit_time" parquet:"exit_time"`
	ExitPriceI64               int64  `json:"exit_price_i64" parquet:"exit_price_i64"`
	ExitSignalID               string `json:"exit_signal_id,omitempty" parquet:"exit_signal_id,optional"`
	ExitOrderID                string `json:"exit_order_id,omitempty" parquet:"exit_order_id,optional"`
	Quantity                   int64  `json:"quantity" parquet:"quantity"`
	GrossPnLI64                int64  `json:"gross_pnl_i64" parquet:"gross_pnl_i64"`
	NetPnLI64                  int64  `json:"net_pnl_i64" parquet:"net_pnl_i64"`
	CommissionI64              int64  `json:"commission_i64" parquet:"commission_i64"`
	SlippageI64                int64  `json:"slippage_i64" parquet:"slippage_i64"`
	MarketL0                   string `json:"market_l0,omitempty" parquet:"market_l0,optional"`
	CenterPhase                string `json:"center_phase,omitempty" parquet:"center_phase,optional"`
	PriceVsCenter              string `json:"price_vs_center,omitempty" parquet:"price_vs_center,optional"`
	TriggerCategory            string `json:"trigger_category,omitempty" parquet:"trigger_category,optional"`
	StructureObjectID          string `json:"structure_object_id,omitempty" parquet:"structure_object_id,optional"`
	StructureObjectRevision    int64  `json:"structure_object_revision,omitempty" parquet:"structure_object_revision,optional"`
	AttributionReasonCode      string `json:"attribution_reason_code,omitempty" parquet:"attribution_reason_code,optional"`
}

type Equity struct {
	BarIndex     int64   `json:"bar_index" parquet:"bar_index"`
	TimestampUTC int64   `json:"timestamp_utc" parquet:"timestamp_utc"`
	TradingDay   string  `json:"trading_day,omitempty" parquet:"trading_day,optional"`
	EquityI64    int64   `json:"equity_i64" parquet:"equity_i64"`
	CashI64      int64   `json:"cash_i64" parquet:"cash_i64"`
	AvailableI64 int64   `json:"available_i64" parquet:"available_i64"`
	MarginI64    int64   `json:"margin_i64" parquet:"margin_i64"`
	Drawdown     float64 `json:"drawdown" parquet:"drawdown"`
}

type eventRow struct {
	EventSeq        int64  `parquet:"event_seq"`
	KnownAtBarIndex int64  `parquet:"known_at_bar_index"`
	ObjectType      string `parquet:"object_type"`
	ObjectID        string `parquet:"object_id"`
	Operation       string `parquet:"operation"`
	ObjectRevision  int64  `parquet:"object_revision"`
	PayloadJSON     string `parquet:"payload_json"`
}

func (s *Service) Trades(runID, cursor string) ([]Trade, *string, error) {
	ref, err := s.resultRef(runID)
	if err != nil {
		return nil, nil, err
	}
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return nil, nil, err
	}
	rows, err := readParquetRows[Trade](filepath.Join(directory, "trades.parquet"))
	if err != nil {
		return nil, nil, err
	}
	offset := decodeCursor(cursor)
	if offset > len(rows) {
		offset = len(rows)
	}
	end := min(offset+100, len(rows))
	var next *string
	if end < len(rows) {
		value := base64.RawURLEncoding.EncodeToString([]byte(strconv.Itoa(end)))
		next = &value
	}
	return rows[offset:end], next, nil
}

func (s *Service) Equity(runID string) ([]Equity, error) {
	ref, err := s.resultRef(runID)
	if err != nil {
		return nil, err
	}
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return nil, err
	}
	return readParquetRows[Equity](filepath.Join(directory, "equity.parquet"))
}

func (s *Service) ChartEvents(runID string) ([]map[string]any, error) {
	ref, err := s.resultRef(runID)
	if err != nil {
		return nil, err
	}
	directory, err := s.guard.Resolve(ref)
	if err != nil {
		return nil, err
	}
	rows, err := readParquetRows[eventRow](filepath.Join(directory, "chart_events.parquet"))
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		payload := map[string]any{}
		if err := json.Unmarshal([]byte(row.PayloadJSON), &payload); err != nil {
			return nil, err
		}
		result = append(result, map[string]any{"event_seq": row.EventSeq, "known_at_bar_index": row.KnownAtBarIndex, "object_type": row.ObjectType, "object_id": row.ObjectID, "operation": row.Operation, "object_revision": row.ObjectRevision, "payload": payload})
	}
	return result, nil
}

// readParquetRows avoids parquet-go's typed reader failure on a valid Arrow
// Parquet file whose only row group has zero rows ("Seek: invalid offset").
// Python deliberately writes these schema-bearing empty files for completed
// runs with no trades or events, so an empty slice is the authoritative result.
func readParquetRows[T any](path string) ([]T, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	parquetFile, err := parquet.OpenFile(file, info.Size())
	if err != nil {
		return nil, err
	}
	if parquetFile.NumRows() == 0 {
		return []T{}, nil
	}
	return parquet.ReadFile[T](path)
}

func decodeCursor(value string) int {
	if value == "" {
		return 0
	}
	data, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		return 0
	}
	offset, err := strconv.Atoi(string(data))
	if err != nil || offset < 0 {
		return 0
	}
	return offset
}
