package calculation

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type Coverage struct {
	FirstBarIndex int64 `json:"first_bar_index"`
	LastBarIndex  int64 `json:"last_bar_index"`
	ReturnedCount int   `json:"returned_count"`
}

type Results struct {
	JobID        string                    `json:"job_id"`
	CacheKey     string                    `json:"cache_key"`
	DatasetID    string                    `json:"dataset_id"`
	DataRevision string                    `json:"data_revision"`
	Algorithm    pythonclient.AlgorithmRef `json:"algorithm"`
	ResultKind   string                    `json:"result_kind"`
	Coverage     Coverage                  `json:"coverage"`
	Checksum     string                    `json:"checksum"`
	BarIndex     []int64                   `json:"bar_index,omitempty"`
	Values       map[string][]*float64     `json:"values,omitempty"`
	Objects      *ChanObjects              `json:"objects,omitempty"`
}

type manifest struct {
	CacheKey     string                    `json:"cache_key"`
	DatasetID    string                    `json:"dataset_id"`
	DataRevision string                    `json:"data_revision"`
	Algorithm    pythonclient.AlgorithmRef `json:"algorithm"`
	Outputs      []string                  `json:"outputs"`
}

func readResults(guard *storage.PathGuard, jobID, cacheKey, resultRef string, from, to int64) (Results, error) {
	directory, err := guard.Resolve(resultRef)
	if err != nil {
		return Results{}, err
	}
	data, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		return Results{}, err
	}
	var meta manifest
	if err := json.Unmarshal(data, &meta); err != nil || meta.CacheKey != cacheKey {
		return Results{}, fmt.Errorf("cache manifest mismatch")
	}
	if meta.Algorithm.Kind == "chan" {
		return readChanResults(directory, jobID, cacheKey, meta, from, to)
	}
	if meta.Algorithm.Kind != "indicator" {
		return Results{}, fmt.Errorf("unsupported calculation result kind %q", meta.Algorithm.Kind)
	}
	file, err := os.Open(filepath.Join(directory, "values.parquet"))
	if err != nil {
		return Results{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return Results{}, err
	}
	parquetFile, err := parquet.OpenFile(file, info.Size())
	if err != nil {
		return Results{}, err
	}
	reader := parquet.NewReader(parquetFile)
	defer reader.Close()
	columns := reader.Schema().Columns()
	columnNames := make([]string, len(columns))
	for index, path := range columns {
		columnNames[index] = path[len(path)-1]
	}
	result := Results{
		JobID: jobID, CacheKey: cacheKey, DatasetID: meta.DatasetID,
		DataRevision: meta.DataRevision, Algorithm: meta.Algorithm, ResultKind: "indicator",
		BarIndex: make([]int64, 0), Values: make(map[string][]*float64, len(meta.Outputs)),
	}
	for _, output := range meta.Outputs {
		result.Values[output] = make([]*float64, 0)
	}
	rows := make([]parquet.Row, 256)
	for {
		count, readErr := reader.ReadRows(rows)
		for _, row := range rows[:count] {
			var barIndex int64
			row.Range(func(columnIndex int, values []parquet.Value) bool {
				if columnNames[columnIndex] == "bar_index" {
					barIndex = values[0].Int64()
				}
				return true
			})
			if barIndex < from || barIndex > to {
				continue
			}
			result.BarIndex = append(result.BarIndex, barIndex)
			row.Range(func(columnIndex int, values []parquet.Value) bool {
				name := columnNames[columnIndex]
				output, wanted := result.Values[name]
				if !wanted {
					return true
				}
				if len(values) == 0 || values[0].IsNull() {
					result.Values[name] = append(output, nil)
				} else {
					value := values[0].Double()
					result.Values[name] = append(output, &value)
				}
				return true
			})
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return Results{}, readErr
		}
	}
	result.Coverage.ReturnedCount = len(result.BarIndex)
	if len(result.BarIndex) > 0 {
		result.Coverage.FirstBarIndex = result.BarIndex[0]
		result.Coverage.LastBarIndex = result.BarIndex[len(result.BarIndex)-1]
	}
	checksumPayload, _ := json.Marshal(struct {
		BarIndex []int64               `json:"bar_index"`
		Values   map[string][]*float64 `json:"values"`
	}{result.BarIndex, result.Values})
	digest := sha256.Sum256(checksumPayload)
	result.Checksum = "sha256:" + hex.EncodeToString(digest[:])
	return result, nil
}

type ChanFractal struct {
	ObjectID            string `json:"object_id" parquet:"object_id"`
	BarIndex            int64  `json:"bar_index" parquet:"bar_index"`
	Time                int64  `json:"time" parquet:"time"`
	PriceI64            int64  `json:"price_i64" parquet:"price_i64"`
	FractalType         string `json:"fractal_type" parquet:"fractal_type"`
	Confirmed           bool   `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64 `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64  `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64  `json:"object_revision" parquet:"object_revision"`
}

type ChanLineObject struct {
	ObjectID            string `json:"object_id" parquet:"object_id"`
	StartBarIndex       int64  `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime           int64  `json:"start_time" parquet:"start_time"`
	StartPriceI64       int64  `json:"start_price_i64" parquet:"start_price_i64"`
	EndBarIndex         int64  `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime             int64  `json:"end_time" parquet:"end_time"`
	EndPriceI64         int64  `json:"end_price_i64" parquet:"end_price_i64"`
	Direction           string `json:"direction" parquet:"direction"`
	Confirmed           bool   `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64 `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64  `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64  `json:"object_revision" parquet:"object_revision"`
}

type ChanZhongshu struct {
	ObjectID            string  `json:"object_id" parquet:"object_id"`
	StartBarIndex       int64   `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime           int64   `json:"start_time" parquet:"start_time"`
	EndBarIndex         int64   `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime             int64   `json:"end_time" parquet:"end_time"`
	ZGI64               int64   `json:"zg_i64" parquet:"zg_i64"`
	ZDI64               int64   `json:"zd_i64" parquet:"zd_i64"`
	GGI64               int64   `json:"gg_i64" parquet:"gg_i64"`
	DDI64               int64   `json:"dd_i64" parquet:"dd_i64"`
	ZI64                int64   `json:"z_i64" parquet:"z_i64"`
	AnalysisLevel       string  `json:"analysis_level" parquet:"analysis_level"`
	ComponentKind       string  `json:"component_kind" parquet:"component_kind"`
	ComponentCount      int64   `json:"component_count" parquet:"component_count"`
	Confirmed           bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	Status              string  `json:"status" parquet:"status"`
	LeaveDirection      *string `json:"leave_direction" parquet:"leave_direction,optional"`
	KnownAtBarIndex     int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanMovementState struct {
	ObjectID            string  `json:"object_id" parquet:"object_id"`
	StartBarIndex       int64   `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime           int64   `json:"start_time" parquet:"start_time"`
	EndBarIndex         int64   `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime             int64   `json:"end_time" parquet:"end_time"`
	PriceI64            int64   `json:"price_i64" parquet:"price_i64"`
	StateType           string  `json:"state_type" parquet:"state_type"`
	Direction           *string `json:"direction" parquet:"direction,optional"`
	AnalysisLevel       string  `json:"analysis_level" parquet:"analysis_level"`
	ReferenceObjectID   string  `json:"reference_object_id" parquet:"reference_object_id"`
	Confirmed           bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanCenterMonitor struct {
	ObjectID            string  `json:"object_id" parquet:"object_id"`
	BarIndex            int64   `json:"bar_index" parquet:"bar_index"`
	Time                int64   `json:"time" parquet:"time"`
	ZI64                int64   `json:"z_i64" parquet:"z_i64"`
	ZnI64               int64   `json:"zn_i64" parquet:"zn_i64"`
	RangeHighI64        int64   `json:"range_high_i64" parquet:"range_high_i64"`
	RangeLowI64         int64   `json:"range_low_i64" parquet:"range_low_i64"`
	ComponentDirection  string  `json:"component_direction" parquet:"component_direction"`
	RelativePosition    string  `json:"relative_position" parquet:"relative_position"`
	Strength            string  `json:"strength" parquet:"strength"`
	MigrationWarning    *string `json:"migration_warning" parquet:"migration_warning,optional"`
	AnalysisLevel       string  `json:"analysis_level" parquet:"analysis_level"`
	ReferenceObjectID   string  `json:"reference_object_id" parquet:"reference_object_id"`
	Confirmed           bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanSignalPoint struct {
	ObjectID            string   `json:"object_id" parquet:"object_id"`
	BarIndex            int64    `json:"bar_index" parquet:"bar_index"`
	Time                int64    `json:"time" parquet:"time"`
	PriceI64            int64    `json:"price_i64" parquet:"price_i64"`
	SignalType          string   `json:"signal_type" parquet:"signal_type"`
	DivergenceKind      *string  `json:"divergence_kind" parquet:"divergence_kind,optional"`
	SignalClass         *string  `json:"signal_class" parquet:"signal_class,optional"`
	Strength            *string  `json:"strength" parquet:"strength,optional"`
	ReferenceObjectID   *string  `json:"reference_object_id" parquet:"reference_object_id,optional"`
	MACDAreaReference   *float64 `json:"macd_area_reference" parquet:"macd_area_reference,optional"`
	MACDAreaCurrent     *float64 `json:"macd_area_current" parquet:"macd_area_current,optional"`
	Confirmed           bool     `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64   `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64    `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64    `json:"object_revision" parquet:"object_revision"`
}

type ChanObjects struct {
	Fractals        []ChanFractal       `json:"fractals"`
	Bi              []ChanLineObject    `json:"bi"`
	Segments        []ChanLineObject    `json:"segments"`
	Zhongshu        []ChanZhongshu      `json:"zhongshu"`
	SegmentZhongshu []ChanZhongshu      `json:"segment_zhongshu"`
	MovementStates  []ChanMovementState `json:"movement_states"`
	CenterMonitors  []ChanCenterMonitor `json:"center_monitors"`
	Divergences     []ChanSignalPoint   `json:"divergences"`
	TradePoints     []ChanSignalPoint   `json:"trade_points"`
}

func readChanResults(directory, jobID, cacheKey string, meta manifest, from, to int64) (Results, error) {
	fractals, err := parquet.ReadFile[ChanFractal](filepath.Join(directory, "fractals.parquet"))
	if err != nil {
		return Results{}, err
	}
	bi, err := parquet.ReadFile[ChanLineObject](filepath.Join(directory, "bi.parquet"))
	if err != nil {
		return Results{}, err
	}
	segments, err := parquet.ReadFile[ChanLineObject](filepath.Join(directory, "segments.parquet"))
	if err != nil {
		return Results{}, err
	}
	zhongshu, err := parquet.ReadFile[ChanZhongshu](filepath.Join(directory, "zhongshu.parquet"))
	if err != nil {
		return Results{}, err
	}
	segmentZhongshu, err := parquet.ReadFile[ChanZhongshu](filepath.Join(directory, "segment_zhongshu.parquet"))
	if err != nil {
		return Results{}, err
	}
	movementStates, err := parquet.ReadFile[ChanMovementState](filepath.Join(directory, "movement_states.parquet"))
	if err != nil {
		return Results{}, err
	}
	centerMonitors, err := parquet.ReadFile[ChanCenterMonitor](filepath.Join(directory, "center_monitors.parquet"))
	if err != nil {
		return Results{}, err
	}
	divergences, err := parquet.ReadFile[ChanSignalPoint](filepath.Join(directory, "divergences.parquet"))
	if err != nil {
		return Results{}, err
	}
	tradePoints, err := parquet.ReadFile[ChanSignalPoint](filepath.Join(directory, "trade_points.parquet"))
	if err != nil {
		return Results{}, err
	}
	objects := ChanObjects{
		Fractals:        filterFractals(fractals, from, to),
		Bi:              filterLines(bi, from, to),
		Segments:        filterLines(segments, from, to),
		Zhongshu:        filterZhongshu(zhongshu, from, to),
		SegmentZhongshu: filterZhongshu(segmentZhongshu, from, to),
		MovementStates:  filterMovementStates(movementStates, from, to),
		CenterMonitors:  filterCenterMonitors(centerMonitors, from, to),
		Divergences:     filterSignalPoints(divergences, from, to),
		TradePoints:     filterSignalPoints(tradePoints, from, to),
	}
	returned := len(objects.Fractals) + len(objects.Bi) + len(objects.Segments) + len(objects.Zhongshu) + len(objects.SegmentZhongshu) + len(objects.MovementStates) + len(objects.CenterMonitors) + len(objects.Divergences) + len(objects.TradePoints)
	checksumPayload, _ := json.Marshal(objects)
	digest := sha256.Sum256(checksumPayload)
	return Results{
		JobID: jobID, CacheKey: cacheKey, DatasetID: meta.DatasetID, DataRevision: meta.DataRevision,
		Algorithm: meta.Algorithm, ResultKind: "chan", Coverage: Coverage{FirstBarIndex: from, LastBarIndex: to, ReturnedCount: returned},
		Checksum: "sha256:" + hex.EncodeToString(digest[:]), Objects: &objects,
	}, nil
}

func filterMovementStates(values []ChanMovementState, from, to int64) []ChanMovementState {
	result := make([]ChanMovementState, 0)
	for _, value := range values {
		if value.EndBarIndex >= from && value.StartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterCenterMonitors(values []ChanCenterMonitor, from, to int64) []ChanCenterMonitor {
	result := make([]ChanCenterMonitor, 0)
	for _, value := range values {
		if value.BarIndex >= from && value.BarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterSignalPoints(values []ChanSignalPoint, from, to int64) []ChanSignalPoint {
	result := make([]ChanSignalPoint, 0)
	for _, value := range values {
		if value.BarIndex >= from && value.BarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterFractals(values []ChanFractal, from, to int64) []ChanFractal {
	result := make([]ChanFractal, 0)
	for _, value := range values {
		if value.BarIndex >= from && value.BarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterLines(values []ChanLineObject, from, to int64) []ChanLineObject {
	result := make([]ChanLineObject, 0)
	for _, value := range values {
		if value.EndBarIndex >= from && value.StartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterZhongshu(values []ChanZhongshu, from, to int64) []ChanZhongshu {
	result := make([]ChanZhongshu, 0)
	for _, value := range values {
		if value.EndBarIndex >= from && value.StartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}
