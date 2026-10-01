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
	ObjectID                  string  `json:"object_id" parquet:"object_id"`
	BarIndex                  int64   `json:"bar_index" parquet:"bar_index"`
	Time                      int64   `json:"time" parquet:"time"`
	PriceI64                  int64   `json:"price_i64" parquet:"price_i64"`
	ZoneLowI64                int64   `json:"zone_low_i64" parquet:"zone_low_i64"`
	ZoneHighI64               int64   `json:"zone_high_i64" parquet:"zone_high_i64"`
	ExtremeSourceBarIndex     int64   `json:"extreme_source_bar_index" parquet:"extreme_source_bar_index"`
	FractalType               string  `json:"fractal_type" parquet:"fractal_type"`
	Status                    string  `json:"status" parquet:"status"`
	InvalidationReason        *string `json:"invalidation_reason" parquet:"invalidation_reason,optional"`
	AuxStrength               string  `json:"aux_strength" parquet:"aux_strength"`
	StrengthReason            string  `json:"strength_reason" parquet:"strength_reason"`
	BodyI64                   *int64  `json:"body_i64" parquet:"body_i64,optional"`
	UpperShadowI64            *int64  `json:"upper_shadow_i64" parquet:"upper_shadow_i64,optional"`
	LowerShadowI64            *int64  `json:"lower_shadow_i64" parquet:"lower_shadow_i64,optional"`
	RangeI64                  *int64  `json:"range_i64" parquet:"range_i64,optional"`
	ClosePositionMilli        *int64  `json:"close_position_milli" parquet:"close_position_milli,optional"`
	FeatureProfile            string  `json:"feature_profile" parquet:"feature_profile"`
	CatalogAlgorithmID        string  `json:"catalog_algorithm_id" parquet:"catalog_algorithm_id"`
	StrengthSemanticNamespace string  `json:"strength_semantic_namespace" parquet:"strength_semantic_namespace"`
	StandardSignal            bool    `json:"standard_signal" parquet:"standard_signal"`
	ExecutionAllowed          bool    `json:"execution_allowed" parquet:"execution_allowed"`
	Confirmed                 bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex       *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex           int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision            int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanLineObject struct {
	ObjectID                   string  `json:"object_id" parquet:"object_id"`
	StartBarIndex              int64   `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime                  int64   `json:"start_time" parquet:"start_time"`
	StartPriceI64              int64   `json:"start_price_i64" parquet:"start_price_i64"`
	StartExtremeSourceBarIndex int64   `json:"start_extreme_source_bar_index" parquet:"start_extreme_source_bar_index"`
	EndBarIndex                int64   `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime                    int64   `json:"end_time" parquet:"end_time"`
	EndPriceI64                int64   `json:"end_price_i64" parquet:"end_price_i64"`
	EndExtremeSourceBarIndex   int64   `json:"end_extreme_source_bar_index" parquet:"end_extreme_source_bar_index"`
	RangeLowI64                int64   `json:"range_low_i64" parquet:"range_low_i64"`
	RangeHighI64               int64   `json:"range_high_i64" parquet:"range_high_i64"`
	RangeLowSourceBarIndex     int64   `json:"range_low_source_bar_index" parquet:"range_low_source_bar_index"`
	RangeHighSourceBarIndex    int64   `json:"range_high_source_bar_index" parquet:"range_high_source_bar_index"`
	RangeProfile               string  `json:"range_profile" parquet:"range_profile"`
	Direction                  string  `json:"direction" parquet:"direction"`
	Status                     string  `json:"status" parquet:"status"`
	InvalidationReason         *string `json:"invalidation_reason" parquet:"invalidation_reason,optional"`
	CatalogAlgorithmID         string  `json:"catalog_algorithm_id" parquet:"catalog_algorithm_id"`
	Confirmed                  bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex        *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex            int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision             int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanProcessedBar struct {
	ObjectID           string  `json:"object_id" parquet:"object_id"`
	NormalizedIndex    int64   `json:"normalized_index" parquet:"normalized_index"`
	StartBarIndex      int64   `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime          int64   `json:"start_time" parquet:"start_time"`
	EndBarIndex        int64   `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime            int64   `json:"end_time" parquet:"end_time"`
	OpenI64            int64   `json:"open_i64" parquet:"open_i64"`
	HighI64            int64   `json:"high_i64" parquet:"high_i64"`
	LowI64             int64   `json:"low_i64" parquet:"low_i64"`
	CloseI64           int64   `json:"close_i64" parquet:"close_i64"`
	HighSourceBarIndex int64   `json:"high_source_bar_index" parquet:"high_source_bar_index"`
	LowSourceBarIndex  int64   `json:"low_source_bar_index" parquet:"low_source_bar_index"`
	Direction          string  `json:"direction" parquet:"direction"`
	SourceBarIndices   []int64 `json:"source_bar_indices" parquet:"source_bar_indices"`
	Status             string  `json:"status" parquet:"status"`
	SealedAtBarIndex   *int64  `json:"sealed_at_bar_index" parquet:"sealed_at_bar_index,optional"`
	CatalogEvent       string  `json:"catalog_event" parquet:"catalog_event"`
	KnownAtBarIndex    int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision     int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanBiState struct {
	ObjectID           string  `json:"object_id" parquet:"object_id"`
	BarIndex           int64   `json:"bar_index" parquet:"bar_index"`
	Time               int64   `json:"time" parquet:"time"`
	PriceI64           int64   `json:"price_i64" parquet:"price_i64"`
	State              string  `json:"state" parquet:"state"`
	Direction          *string `json:"direction" parquet:"direction,optional"`
	AnchorFractalID    *string `json:"anchor_fractal_id" parquet:"anchor_fractal_id,optional"`
	CandidateObjectID  *string `json:"candidate_object_id" parquet:"candidate_object_id,optional"`
	Trigger            string  `json:"trigger" parquet:"trigger"`
	CatalogAlgorithmID string  `json:"catalog_algorithm_id" parquet:"catalog_algorithm_id"`
	KnownAtBarIndex    int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision     int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanLocalCenter struct {
	PreviousCenterID          *string  `json:"previous_center_id" parquet:"previous_center_id,optional"`
	FormationDir              *string  `json:"formation_dir,omitempty" parquet:"formation_dir,optional"`
	RelativeDir               *string  `json:"relative_dir,omitempty" parquet:"relative_dir,optional"`
	DDI64                     *int64   `json:"dd_i64,omitempty" parquet:"dd_i64,optional"`
	GGI64                     *int64   `json:"gg_i64,omitempty" parquet:"gg_i64,optional"`
	ComparisonDDI64           *int64   `json:"comparison_dd_i64,omitempty" parquet:"comparison_dd_i64,optional"`
	ComparisonGGI64           *int64   `json:"comparison_gg_i64,omitempty" parquet:"comparison_gg_i64,optional"`
	ComparisonExcludedEntryID *string  `json:"comparison_excluded_entry_id,omitempty" parquet:"comparison_excluded_entry_id,optional"`
	CoreRelation              *string  `json:"core_relation" parquet:"core_relation,optional"`
	HigherLevelReviewRequired bool     `json:"higher_level_review_required" parquet:"higher_level_review_required"`
	TrendStatus               string   `json:"trend_status" parquet:"trend_status"`
	ObjectID                  string   `json:"object_id" parquet:"object_id"`
	StreamKey                 string   `json:"stream_key" parquet:"stream_key"`
	RuleVersion               string   `json:"rule_version" parquet:"rule_version"`
	UnitKind                  string   `json:"unit_kind" parquet:"unit_kind"`
	StructuralLevel           string   `json:"structural_level" parquet:"structural_level"`
	ScanFloor                 int64    `json:"scan_floor" parquet:"scan_floor"`
	SeedIDs                   []string `json:"seed_ids" parquet:"seed_ids"`
	ZDI64                     int64    `json:"zd_i64" parquet:"zd_i64"`
	ZGI64                     int64    `json:"zg_i64" parquet:"zg_i64"`
	SeedStartBarIndex         int64    `json:"seed_start_bar_index" parquet:"seed_start_bar_index"`
	SeedStartTime             int64    `json:"seed_start_time" parquet:"seed_start_time"`
	SeedEndBarIndex           int64    `json:"seed_end_bar_index" parquet:"seed_end_bar_index"`
	SeedEndTime               int64    `json:"seed_end_time" parquet:"seed_end_time"`
	FormedAtBarIndex          int64    `json:"formed_at_bar_index" parquet:"formed_at_bar_index"`
	BodyStartBarIndex         int64    `json:"body_start_bar_index" parquet:"body_start_bar_index"`
	BodyStartTime             int64    `json:"body_start_time" parquet:"body_start_time"`
	BodyEndBarIndex           *int64   `json:"body_end_bar_index" parquet:"body_end_bar_index,optional"`
	BodyEndTime               *int64   `json:"body_end_time" parquet:"body_end_time,optional"`
	ObservedStartBarIndex     int64    `json:"observed_start_bar_index" parquet:"observed_start_bar_index"`
	ObservedStartTime         int64    `json:"observed_start_time" parquet:"observed_start_time"`
	ObservedEndBarIndex       int64    `json:"observed_end_bar_index" parquet:"observed_end_bar_index"`
	ObservedEndTime           int64    `json:"observed_end_time" parquet:"observed_end_time"`
	ObservedLowI64            int64    `json:"observed_low_i64" parquet:"observed_low_i64"`
	ObservedHighI64           int64    `json:"observed_high_i64" parquet:"observed_high_i64"`
	Status                    string   `json:"status" parquet:"status"`
	PendingExitID             *string  `json:"pending_exit_id" parquet:"pending_exit_id,optional"`
	ExitID                    *string  `json:"exit_id" parquet:"exit_id,optional"`
	FirstRetestID             *string  `json:"first_retest_id" parquet:"first_retest_id,optional"`
	EntryID                   *string  `json:"entry_id" parquet:"entry_id,optional"`
	LocalEntry                *string  `json:"local_entry" parquet:"local_entry,optional"`
	BreakDirection            *string  `json:"break_direction" parquet:"break_direction,optional"`
	BreakConfirmedAtBarIndex  *int64   `json:"break_confirmed_at_bar_index" parquet:"break_confirmed_at_bar_index,optional"`
	ParentID                  *string  `json:"parent_id" parquet:"parent_id,optional"`
	LeftContextIncomplete     bool     `json:"left_context_incomplete" parquet:"left_context_incomplete"`
	RolesOverlapSeed          bool     `json:"roles_overlap_seed" parquet:"roles_overlap_seed"`
	SourceRevision            string   `json:"source_revision" parquet:"source_revision"`
	KnownAtBarIndex           int64    `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision            int64    `json:"object_revision" parquet:"object_revision"`
}

type ChanCenterConnection struct {
	ObjectID            string   `json:"object_id" parquet:"object_id"`
	StreamKey           string   `json:"stream_key" parquet:"stream_key"`
	RuleVersion         string   `json:"rule_version" parquet:"rule_version"`
	UnitKind            string   `json:"unit_kind" parquet:"unit_kind"`
	StructuralLevel     string   `json:"structural_level" parquet:"structural_level"`
	FromCenterID        string   `json:"from_center_id" parquet:"from_center_id"`
	ToCenterID          *string  `json:"to_center_id" parquet:"to_center_id,optional"`
	OrderedUnitIDs      []string `json:"ordered_unit_ids" parquet:"ordered_unit_ids"`
	ExitUnitID          string   `json:"exit_unit_id" parquet:"exit_unit_id"`
	EntryUnitID         *string  `json:"entry_unit_id" parquet:"entry_unit_id,optional"`
	FirstRetestID       string   `json:"first_retest_id" parquet:"first_retest_id"`
	StartBarIndex       int64    `json:"start_bar_index" parquet:"start_bar_index"`
	StartTime           int64    `json:"start_time" parquet:"start_time"`
	EndBarIndex         int64    `json:"end_bar_index" parquet:"end_bar_index"`
	EndTime             int64    `json:"end_time" parquet:"end_time"`
	ConfirmedAtBarIndex int64    `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index"`
	RolesOverlapSeed    bool     `json:"roles_overlap_seed" parquet:"roles_overlap_seed"`
	SourceRevision      string   `json:"source_revision" parquet:"source_revision"`
	KnownAtBarIndex     int64    `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64    `json:"object_revision" parquet:"object_revision"`
}

type ChanCenterAuditEvent struct {
	PreviewState     *string  `json:"preview_state" parquet:"preview_state,optional"`
	PreviewConfirmed *bool    `json:"preview_confirmed" parquet:"preview_confirmed,optional"`
	PreviewDirection *string  `json:"preview_direction" parquet:"preview_direction,optional"`
	SourceRevision   *string  `json:"source_revision" parquet:"source_revision,optional"`
	ObjectID         string   `json:"object_id" parquet:"object_id"`
	EventType        string   `json:"event_type" parquet:"event_type"`
	CenterID         string   `json:"center_id" parquet:"center_id"`
	UnitIDs          []string `json:"unit_ids" parquet:"unit_ids"`
	ZDI64            int64    `json:"zd_i64" parquet:"zd_i64"`
	ZGI64            int64    `json:"zg_i64" parquet:"zg_i64"`
	ComparisonI64    *int64   `json:"comparison_i64" parquet:"comparison_i64,optional"`
	EventBarIndex    int64    `json:"event_bar_index" parquet:"event_bar_index"`
	EventTime        int64    `json:"event_time" parquet:"event_time"`
	RuleVersion      string   `json:"rule_version" parquet:"rule_version"`
	SourceFile       string   `json:"source_file" parquet:"source_file"`
	SourceLine       int64    `json:"source_line" parquet:"source_line"`
	KnownAtBarIndex  int64    `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision   int64    `json:"object_revision" parquet:"object_revision"`
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
	ZTwiceI64           int64   `json:"z_twice_i64" parquet:"z_twice_i64"`
	ZnTwiceI64          int64   `json:"zn_twice_i64" parquet:"zn_twice_i64"`
	CoreLowI64          int64   `json:"core_low_i64" parquet:"core_low_i64"`
	CoreHighI64         int64   `json:"core_high_i64" parquet:"core_high_i64"`
	RangeHighI64        int64   `json:"range_high_i64" parquet:"range_high_i64"`
	RangeLowI64         int64   `json:"range_low_i64" parquet:"range_low_i64"`
	ComponentOrdinal    int64   `json:"component_ordinal" parquet:"component_ordinal"`
	ComponentDirection  string  `json:"component_direction" parquet:"component_direction"`
	RelativePosition    string  `json:"relative_position" parquet:"relative_position"`
	OscillationBias     string  `json:"oscillation_bias" parquet:"oscillation_bias"`
	BreakoutWarning     *string `json:"breakout_warning" parquet:"breakout_warning,optional"`
	CatalogAlgorithmID  string  `json:"catalog_algorithm_id" parquet:"catalog_algorithm_id"`
	SemanticNamespace   string  `json:"semantic_namespace" parquet:"semantic_namespace"`
	EvidenceLevel       string  `json:"evidence_level" parquet:"evidence_level"`
	LevelMappingProfile string  `json:"level_mapping_profile" parquet:"level_mapping_profile"`
	StandardSignal      bool    `json:"standard_signal" parquet:"standard_signal"`
	ExecutionAllowed    bool    `json:"execution_allowed" parquet:"execution_allowed"`
	ConfirmsThirdPoint  bool    `json:"confirms_third_point" parquet:"confirms_third_point"`
	AnalysisLevel       string  `json:"analysis_level" parquet:"analysis_level"`
	ReferenceObjectID   string  `json:"reference_object_id" parquet:"reference_object_id"`
	Confirmed           bool    `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex *int64  `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex     int64   `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision      int64   `json:"object_revision" parquet:"object_revision"`
}

type ChanSignalPoint struct {
	ObjectID                      string   `json:"object_id" parquet:"object_id"`
	BarIndex                      int64    `json:"bar_index" parquet:"bar_index"`
	Time                          int64    `json:"time" parquet:"time"`
	PriceI64                      int64    `json:"price_i64" parquet:"price_i64"`
	SignalType                    string   `json:"signal_type" parquet:"signal_type"`
	DivergenceKind                *string  `json:"divergence_kind" parquet:"divergence_kind,optional"`
	DivergenceProfile             *string  `json:"divergence_profile,omitempty" parquet:"divergence_profile,optional"`
	FormationDir                  *string  `json:"formation_dir,omitempty" parquet:"formation_dir,optional"`
	RelativeDir                   *string  `json:"relative_dir,omitempty" parquet:"relative_dir,optional"`
	AObjectID                     *string  `json:"a_object_id,omitempty" parquet:"a_object_id,optional"`
	BObjectID                     *string  `json:"b_object_id,omitempty" parquet:"b_object_id,optional"`
	ACenterID                     *string  `json:"a_center_id,omitempty" parquet:"a_center_id,optional"`
	BCenterID                     *string  `json:"b_center_id,omitempty" parquet:"b_center_id,optional"`
	MACDAreaRatio                 *float64 `json:"macd_area_ratio,omitempty" parquet:"macd_area_ratio,optional"`
	MACDDiffReferenceExtreme      *float64 `json:"macd_diff_reference_extreme,omitempty" parquet:"macd_diff_reference_extreme,optional"`
	MACDDiffCurrentExtreme        *float64 `json:"macd_diff_current_extreme,omitempty" parquet:"macd_diff_current_extreme,optional"`
	MACDDEAReferenceExtreme       *float64 `json:"macd_dea_reference_extreme,omitempty" parquet:"macd_dea_reference_extreme,optional"`
	MACDDEACurrentExtreme         *float64 `json:"macd_dea_current_extreme,omitempty" parquet:"macd_dea_current_extreme,optional"`
	MACDExtremeRelation           *string  `json:"macd_extreme_relation,omitempty" parquet:"macd_extreme_relation,optional"`
	MACDParameterProfile          *string  `json:"macd_parameter_profile,omitempty" parquet:"macd_parameter_profile,optional"`
	CContainsType3                *bool    `json:"c_contains_type3,omitempty" parquet:"c_contains_type3,optional"`
	CMeetsSublevel                *bool    `json:"c_meets_sublevel,omitempty" parquet:"c_meets_sublevel,optional"`
	CSublevelProfile              *string  `json:"c_sublevel_profile,omitempty" parquet:"c_sublevel_profile,optional"`
	CSublevelCenterIDs            []string `json:"c_sublevel_center_ids,omitempty" parquet:"c_sublevel_center_ids,optional"`
	CType3DepartureID             *string  `json:"c_type3_departure_id,omitempty" parquet:"c_type3_departure_id,optional"`
	CType3RetestID                *string  `json:"c_type3_retest_id,omitempty" parquet:"c_type3_retest_id,optional"`
	CProofKnownAtBarIndex         *int64   `json:"c_proof_known_at_bar_index,omitempty" parquet:"c_proof_known_at_bar_index,optional"`
	SignalClass                   *string  `json:"signal_class" parquet:"signal_class,optional"`
	Strength                      *string  `json:"strength" parquet:"strength,optional"`
	ReferenceObjectID             *string  `json:"reference_object_id" parquet:"reference_object_id,optional"`
	MACDAreaReference             *float64 `json:"macd_area_reference" parquet:"macd_area_reference,optional"`
	MACDAreaCurrent               *float64 `json:"macd_area_current" parquet:"macd_area_current,optional"`
	Status                        string   `json:"status" parquet:"status"`
	InvalidationReason            *string  `json:"invalidation_reason" parquet:"invalidation_reason,optional"`
	LevelID                       *string  `json:"level_id" parquet:"level_id,optional"`
	LowerLevelTurnObjectID        *string  `json:"lower_level_turn_object_id" parquet:"lower_level_turn_object_id,optional"`
	CatalogEvent                  *string  `json:"catalog_event" parquet:"catalog_event,optional"`
	CatalogAlgorithmID            *string  `json:"catalog_algorithm_id" parquet:"catalog_algorithm_id,optional"`
	EvidenceProfile               string   `json:"evidence_profile" parquet:"evidence_profile"`
	ComparisonReferenceObjectID   *string  `json:"comparison_reference_object_id" parquet:"comparison_reference_object_id,optional"`
	ComparisonCurrentObjectID     *string  `json:"comparison_current_object_id" parquet:"comparison_current_object_id,optional"`
	ComparisonRule                *string  `json:"comparison_rule" parquet:"comparison_rule,optional"`
	StrengthProfile               *string  `json:"strength_profile,omitempty" parquet:"strength_profile,optional"`
	StrengthRelation              *string  `json:"strength_relation,omitempty" parquet:"strength_relation,optional"`
	StrengthTrigger               *string  `json:"strength_trigger,omitempty" parquet:"strength_trigger,optional"`
	PriceDisplacementReferenceI64 *int64   `json:"price_displacement_reference_i64,omitempty" parquet:"price_displacement_reference_i64,optional"`
	PriceDisplacementCurrentI64   *int64   `json:"price_displacement_current_i64,omitempty" parquet:"price_displacement_current_i64,optional"`
	ObservedIntervalsReference    *int64   `json:"observed_intervals_reference,omitempty" parquet:"observed_intervals_reference,optional"`
	ObservedIntervalsCurrent      *int64   `json:"observed_intervals_current,omitempty" parquet:"observed_intervals_current,optional"`
	BaselineSpanReferenceI64      *int64   `json:"baseline_span_reference_i64,omitempty" parquet:"baseline_span_reference_i64,optional"`
	BaselineSpanCurrentI64        *int64   `json:"baseline_span_current_i64,omitempty" parquet:"baseline_span_current_i64,optional"`
	BaselineSpanBelow80Pct        *bool    `json:"baseline_span_below_80pct,omitempty" parquet:"baseline_span_below_80pct,optional"`
	NewExtremeSatisfied           *bool    `json:"new_extreme_satisfied" parquet:"new_extreme_satisfied,optional"`
	DepartureObjectID             *string  `json:"departure_object_id" parquet:"departure_object_id,optional"`
	ReturnObjectID                *string  `json:"return_object_id" parquet:"return_object_id,optional"`
	ReturnOrdinal                 *int64   `json:"return_ordinal" parquet:"return_ordinal,optional"`
	BoundaryProfile               *string  `json:"boundary_profile" parquet:"boundary_profile,optional"`
	BoundaryRelation              *string  `json:"boundary_relation" parquet:"boundary_relation,optional"`
	ReturnDepthToCoreI64          *int64   `json:"return_depth_to_core_i64" parquet:"return_depth_to_core_i64,optional"`
	ReturnDepthToOuterI64         *int64   `json:"return_depth_to_outer_i64" parquet:"return_depth_to_outer_i64,optional"`
	FollowThroughObjectID         *string  `json:"follow_through_object_id" parquet:"follow_through_object_id,optional"`
	FollowThroughStatus           string   `json:"follow_through_status" parquet:"follow_through_status"`
	ConfirmationLatencyBars       int64    `json:"confirmation_latency_bars" parquet:"confirmation_latency_bars"`
	ReferenceCenterOrdinal        *int64   `json:"reference_center_ordinal" parquet:"reference_center_ordinal,optional"`
	OlderCenterCount              *int64   `json:"older_center_count" parquet:"older_center_count,optional"`
	CenterChainProfile            *string  `json:"center_chain_profile" parquet:"center_chain_profile,optional"`
	Confirmed                     bool     `json:"confirmed" parquet:"confirmed"`
	ConfirmedAtBarIndex           *int64   `json:"confirmed_at_bar_index" parquet:"confirmed_at_bar_index,optional"`
	KnownAtBarIndex               int64    `json:"known_at_bar_index" parquet:"known_at_bar_index"`
	ObjectRevision                int64    `json:"object_revision" parquet:"object_revision"`
}

type ChanObjects struct {
	ProcessedBars     []ChanProcessedBar     `json:"processed_bars"`
	Fractals          []ChanFractal          `json:"fractals"`
	Bi                []ChanLineObject       `json:"bi"`
	BiStates          []ChanBiState          `json:"bi_states"`
	Segments          []ChanLineObject       `json:"segments"`
	LocalCenters      []ChanLocalCenter      `json:"local_centers"`
	CenterConnections []ChanCenterConnection `json:"center_connections"`
	CenterAuditEvents []ChanCenterAuditEvent `json:"center_audit_events"`
	MovementStates    []ChanMovementState    `json:"movement_states"`
	CenterMonitors    []ChanCenterMonitor    `json:"center_monitors"`
	Divergences       []ChanSignalPoint      `json:"divergences"`
	TradePoints       []ChanSignalPoint      `json:"trade_points"`
}

func readChanResults(directory, jobID, cacheKey string, meta manifest, from, to int64) (Results, error) {
	processedBars, err := parquet.ReadFile[ChanProcessedBar](filepath.Join(directory, "processed_bars.parquet"))
	if err != nil {
		return Results{}, err
	}
	fractals, err := parquet.ReadFile[ChanFractal](filepath.Join(directory, "fractals.parquet"))
	if err != nil {
		return Results{}, err
	}
	bi, err := parquet.ReadFile[ChanLineObject](filepath.Join(directory, "bi.parquet"))
	if err != nil {
		return Results{}, err
	}
	biStates, err := parquet.ReadFile[ChanBiState](filepath.Join(directory, "bi_states.parquet"))
	if err != nil {
		return Results{}, err
	}
	segments, err := parquet.ReadFile[ChanLineObject](filepath.Join(directory, "segments.parquet"))
	if err != nil {
		return Results{}, err
	}
	localCenters, err := parquet.ReadFile[ChanLocalCenter](filepath.Join(directory, "local_centers.parquet"))
	if err != nil {
		return Results{}, err
	}
	centerConnections, err := parquet.ReadFile[ChanCenterConnection](filepath.Join(directory, "center_connections.parquet"))
	if err != nil {
		return Results{}, err
	}
	centerAuditEvents, err := parquet.ReadFile[ChanCenterAuditEvent](filepath.Join(directory, "center_audit_events.parquet"))
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
		ProcessedBars:     filterProcessedBars(processedBars, from, to),
		Fractals:          filterFractals(fractals, from, to),
		Bi:                filterLines(bi, from, to),
		BiStates:          filterBiStates(biStates, from, to),
		Segments:          filterLines(segments, from, to),
		LocalCenters:      filterLocalCenters(localCenters, from, to),
		CenterConnections: filterCenterConnections(centerConnections, from, to),
		CenterAuditEvents: filterCenterAuditEvents(centerAuditEvents, from, to),
		MovementStates:    filterMovementStates(movementStates, from, to),
		CenterMonitors:    filterCenterMonitors(centerMonitors, from, to),
		Divergences:       filterSignalPoints(divergences, from, to),
		TradePoints:       filterSignalPoints(tradePoints, from, to),
	}
	returned := len(objects.ProcessedBars) + len(objects.Fractals) + len(objects.Bi) + len(objects.BiStates) + len(objects.Segments) + len(objects.LocalCenters) + len(objects.CenterConnections) + len(objects.CenterAuditEvents) + len(objects.MovementStates) + len(objects.CenterMonitors) + len(objects.Divergences) + len(objects.TradePoints)
	checksumPayload, _ := json.Marshal(objects)
	digest := sha256.Sum256(checksumPayload)
	return Results{
		JobID: jobID, CacheKey: cacheKey, DatasetID: meta.DatasetID, DataRevision: meta.DataRevision,
		Algorithm: meta.Algorithm, ResultKind: "chan", Coverage: Coverage{FirstBarIndex: from, LastBarIndex: to, ReturnedCount: returned},
		Checksum: "sha256:" + hex.EncodeToString(digest[:]), Objects: &objects,
	}, nil
}

func filterProcessedBars(values []ChanProcessedBar, from, to int64) []ChanProcessedBar {
	result := make([]ChanProcessedBar, 0)
	for _, value := range values {
		if value.EndBarIndex >= from && value.StartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterBiStates(values []ChanBiState, from, to int64) []ChanBiState {
	result := make([]ChanBiState, 0)
	for _, value := range values {
		if value.BarIndex >= from && value.BarIndex <= to {
			result = append(result, value)
		}
	}
	return result
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

func filterLocalCenters(values []ChanLocalCenter, from, to int64) []ChanLocalCenter {
	result := make([]ChanLocalCenter, 0)
	for _, value := range values {
		end := value.ObservedEndBarIndex
		if value.BodyEndBarIndex != nil {
			end = *value.BodyEndBarIndex
		}
		if end >= from && value.BodyStartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterCenterConnections(values []ChanCenterConnection, from, to int64) []ChanCenterConnection {
	result := make([]ChanCenterConnection, 0)
	for _, value := range values {
		if value.EndBarIndex >= from && value.StartBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}

func filterCenterAuditEvents(values []ChanCenterAuditEvent, from, to int64) []ChanCenterAuditEvent {
	result := make([]ChanCenterAuditEvent, 0)
	for _, value := range values {
		if value.EventBarIndex >= from && value.EventBarIndex <= to {
			result = append(result, value)
		}
	}
	return result
}
