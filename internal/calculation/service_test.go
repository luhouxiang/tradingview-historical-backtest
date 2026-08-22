package calculation

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var testAlgorithm = pythonclient.AlgorithmRef{
	Kind: "indicator", AlgorithmID: "ma", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("2", 64),
}
var testChanAlgorithm = pythonclient.AlgorithmRef{
	Kind: "chan", AlgorithmID: "chan_standard", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("4", 64),
}

type fakeCatalog struct{ meta catalog.DatasetMeta }

func (f fakeCatalog) Get(datasetID, revision string) (catalog.DatasetMeta, error) {
	if datasetID != f.meta.DatasetID {
		return catalog.DatasetMeta{}, catalog.ErrNotFound
	}
	return f.meta, nil
}

type fakePython struct {
	mu      sync.Mutex
	submits int
}

func (f *fakePython) Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error) {
	return []pythonclient.AlgorithmDefinition{{
		AlgorithmRef: testAlgorithm,
		ParameterSchema: map[string]any{"properties": map[string]any{
			"period": map[string]any{"type": "integer", "minimum": float64(1), "maximum": float64(100), "default": float64(20)},
			"source": map[string]any{"type": "string", "enum": []any{"close"}, "default": "close"},
		}},
	}, {
		AlgorithmRef: testChanAlgorithm,
		ParameterSchema: map[string]any{"properties": map[string]any{
			"min_fractal_gap":     map[string]any{"type": "integer", "minimum": float64(1), "maximum": float64(100), "default": float64(5)},
			"checkpoint_interval": map[string]any{"type": "integer", "minimum": float64(1), "maximum": float64(10000), "default": float64(1024)},
		}},
	}}, nil
}

func (f *fakePython) Submit(context.Context, string, string, string, any) (pythonclient.JobStatus, error) {
	f.mu.Lock()
	f.submits++
	f.mu.Unlock()
	return pythonclient.JobStatus{Status: "queued"}, nil
}
func (f *fakePython) Job(context.Context, string, string, string) (pythonclient.JobStatus, error) {
	return pythonclient.JobStatus{Status: "running", Progress: .5}, nil
}
func (f *fakePython) Cancel(context.Context, string, string, string) error { return nil }
func (f *fakePython) Health(context.Context) pythonclient.Health {
	return pythonclient.Health{Status: "ok", ContractVersion: "1.0.0", Services: map[string]struct {
		Status  string `json:"status"`
		Version string `json:"version"`
	}{"python-engine": {Status: "ok", Version: "0.1.0"}}}
}

func TestNormalizeDefaultsAndCacheInvalidationMatrix(t *testing.T) {
	schema := map[string]any{"properties": map[string]any{
		"period": map[string]any{"type": "integer", "minimum": float64(1), "default": float64(20)},
		"source": map[string]any{"type": "string", "enum": []any{"close"}, "default": "close"},
	}}
	defaults, err := NormalizeParameters(schema, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	explicit, err := NormalizeParameters(schema, map[string]any{"source": "close", "period": int64(20)})
	if err != nil {
		t.Fatal(err)
	}
	base, _ := CacheKey("sha256:"+repeat("1", 64), testAlgorithm, defaults, "full_history", "0.1.0")
	equal, _ := CacheKey("sha256:"+repeat("1", 64), testAlgorithm, explicit, "full_history", "0.1.0")
	if base != equal {
		t.Fatal("defaults and explicit parameters must share a cache key")
	}
	variants := []struct {
		revision, mode, engine string
		algorithm              pythonclient.AlgorithmRef
		parameters             map[string]any
	}{
		{"sha256:" + repeat("9", 64), "full_history", "0.1.0", testAlgorithm, defaults},
		{"sha256:" + repeat("1", 64), "causal_events", "0.1.0", testAlgorithm, defaults},
		{"sha256:" + repeat("1", 64), "full_history", "0.2.0", testAlgorithm, defaults},
		{"sha256:" + repeat("1", 64), "full_history", "0.1.0", pythonclient.AlgorithmRef{Kind: "indicator", AlgorithmID: "ma", AlgorithmVersion: "2.0.0", SourceHash: testAlgorithm.SourceHash}, defaults},
		{"sha256:" + repeat("1", 64), "full_history", "0.1.0", pythonclient.AlgorithmRef{Kind: "indicator", AlgorithmID: "ma", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("8", 64)}, defaults},
		{"sha256:" + repeat("1", 64), "full_history", "0.1.0", testAlgorithm, map[string]any{"period": int64(21), "source": "close"}},
	}
	for index, variant := range variants {
		key, _ := CacheKey(variant.revision, variant.algorithm, variant.parameters, variant.mode, variant.engine)
		if key == base {
			t.Fatalf("variant %d did not invalidate cache", index)
		}
	}
}

func TestConcurrentEquivalentRequestsUseOnePythonSubmission(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	python := &fakePython{}
	meta := catalog.DatasetMeta{DatasetID: "TEST.A1.1m", DataRevision: "sha256:" + repeat("1", 64), Files: []catalog.FileMeta{{Role: "bars", Path: "normalized/test/bars.parquet"}}}
	service := NewService(guard, fakeCatalog{meta}, python, jobs.NewManager(), "1.0.0", time.Hour)
	request := Request{DatasetID: meta.DatasetID, DataRevision: meta.DataRevision, Algorithm: testAlgorithm, Parameters: map[string]any{}, CalculationMode: "full_history"}
	const count = 20
	ids := make(chan string, count)
	var wait sync.WaitGroup
	for range count {
		wait.Add(1)
		go func() {
			defer wait.Done()
			result, err := service.Submit(context.Background(), "request", "trace", request)
			if err != nil {
				t.Errorf("submit: %v", err)
				return
			}
			ids <- result.Job.ID
		}()
	}
	wait.Wait()
	close(ids)
	first := ""
	for id := range ids {
		if first == "" {
			first = id
		} else if id != first {
			t.Fatalf("singleflight returned %q and %q", first, id)
		}
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		python.mu.Lock()
		submits := python.submits
		python.mu.Unlock()
		if submits == 1 {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("Python submission count was not one")
}

type resultRow struct {
	BarIndex int64    `parquet:"bar_index"`
	MA       *float64 `parquet:"ma,optional"`
}

func TestCompletedCacheHitAndRangeRead(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	python := &fakePython{}
	meta := catalog.DatasetMeta{DatasetID: "TEST.A1.1m", DataRevision: "sha256:" + repeat("1", 64), Files: []catalog.FileMeta{{Role: "bars", Path: "normalized/test/bars.parquet"}}}
	service := NewService(guard, fakeCatalog{meta}, python, jobs.NewManager(), "1.0.0", time.Millisecond)
	parameters := map[string]any{"period": int64(20), "source": "close"}
	key, _ := CacheKey(meta.DataRevision, testAlgorithm, parameters, "full_history", "0.1.0")
	ref := "cache/indicators/" + key[len("sha256:"):]
	directory, _ := guard.Resolve(ref)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	manifestData, _ := json.Marshal(map[string]any{"cache_key": key, "dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "algorithm": testAlgorithm, "outputs": []string{"ma"}})
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifestData, 0o640); err != nil {
		t.Fatal(err)
	}
	one, three := 1.0, 3.0
	if err := parquet.WriteFile(filepath.Join(directory, "values.parquet"), []resultRow{{0, nil}, {1, &one}, {2, &three}}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "_SUCCESS"), nil, 0o640); err != nil {
		t.Fatal(err)
	}
	request := Request{DatasetID: meta.DatasetID, DataRevision: meta.DataRevision, Algorithm: testAlgorithm, Parameters: map[string]any{}, CalculationMode: "full_history"}
	created, err := service.Submit(context.Background(), "request", "trace", request)
	if err != nil || !created.CacheHit || created.Job.Status != jobs.Completed {
		t.Fatalf("expected completed cache hit: %#v %v", created, err)
	}
	result, err := service.Results(created.Job.ID, 0, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.BarIndex) != 3 || result.Values["ma"][0] != nil || *result.Values["ma"][2] != 3 {
		t.Fatalf("unexpected range result: %#v", result)
	}
}

func TestIncompleteCacheIsNeverAHit(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	service := NewService(guard, fakeCatalog{}, &fakePython{}, jobs.NewManager(), "1.0.0", time.Hour)
	key := "sha256:" + repeat("3", 64)
	ref := "cache/indicators/" + key[len("sha256:"):]
	directory, _ := guard.Resolve(ref)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]any{"cache_key": key})
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), data, 0o640); err != nil {
		t.Fatal(err)
	}
	if service.validCache(ref, key) {
		t.Fatal("cache without _SUCCESS must not be a hit")
	}
}

type chanEventTestRow struct {
	EventSeq        int64  `parquet:"event_seq"`
	KnownAtBarIndex int64  `parquet:"known_at_bar_index"`
	ObjectType      string `parquet:"object_type"`
	ObjectID        string `parquet:"object_id"`
	Operation       string `parquet:"operation"`
	ObjectRevision  int64  `parquet:"object_revision"`
	PayloadJSON     string `parquet:"payload_json"`
}

func TestChanCacheHitAndSemanticRangeRead(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	python := &fakePython{}
	meta := catalog.DatasetMeta{DatasetID: "TEST.A1.1m", DataRevision: "sha256:" + repeat("1", 64), Files: []catalog.FileMeta{{Role: "bars", Path: "normalized/test/bars.parquet"}}}
	service := NewService(guard, fakeCatalog{meta}, python, jobs.NewManager(), "1.0.0", time.Millisecond)
	parameters := map[string]any{"min_fractal_gap": int64(5), "checkpoint_interval": int64(1024)}
	key, _ := CacheKey(meta.DataRevision, testChanAlgorithm, parameters, "causal_events", "0.1.0")
	ref := "cache/chan/" + key[len("sha256:"):]
	directory, _ := guard.Resolve(ref)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	manifestData, _ := json.Marshal(map[string]any{"cache_key": key, "dataset_id": meta.DatasetID, "data_revision": meta.DataRevision, "algorithm": testChanAlgorithm})
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifestData, 0o640); err != nil {
		t.Fatal(err)
	}
	confirmedAt := int64(14)
	processedBars := []ChanProcessedBar{{ObjectID: "processed-bar-1", NormalizedIndex: 1, StartBarIndex: 14, StartTime: 1400, EndBarIndex: 16, EndTime: 1600, OpenI64: 105, HighI64: 108, LowI64: 98, CloseI64: 100, HighSourceBarIndex: 14, LowSourceBarIndex: 16, Direction: "down", SourceBarIndices: []int64{14, 15, 16}, Status: "sealed", SealedAtBarIndex: &confirmedAt, CatalogEvent: "processed_bar_revision", KnownAtBarIndex: 16, ObjectRevision: 2}}
	fractals := []ChanFractal{{ObjectID: "fractal-1", BarIndex: 10, Time: 1000, PriceI64: 110, ZoneLowI64: 100, ZoneHighI64: 110, ExtremeSourceBarIndex: 10, FractalType: "top", Status: "confirmed", AuxStrength: "unclassified", StrengthReason: "lesson_82_numeric_profile_not_matched", CatalogAlgorithmID: "ALG-GEO-002", StrengthSemanticNamespace: "auxiliary", Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 12, ObjectRevision: 1}}
	lines := []ChanLineObject{{ObjectID: "bi-1", StartBarIndex: 10, StartTime: 1000, StartPriceI64: 110, StartExtremeSourceBarIndex: 10, EndBarIndex: 20, EndTime: 2000, EndPriceI64: 90, EndExtremeSourceBarIndex: 20, Direction: "down", Status: "confirmed", CatalogAlgorithmID: "ALG-GEO-003", Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 14, ObjectRevision: 2}}
	direction := "down"
	anchorID := "fractal-1"
	biStates := []ChanBiState{{ObjectID: "bi-state-current", BarIndex: 20, Time: 2000, PriceI64: 90, State: "DOWN_EXTENDING", Direction: &direction, AnchorFractalID: &anchorID, Trigger: "bi_confirmed", CatalogAlgorithmID: "ALG-GEO-003", KnownAtBarIndex: 20, ObjectRevision: 3}}
	centres := []ChanZhongshu{{ObjectID: "zhongshu-1", StartBarIndex: 18, StartTime: 1800, EndBarIndex: 30, EndTime: 3000, ZGI64: 100, ZDI64: 100, Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 30, ObjectRevision: 1}}
	levelID := "L0"
	catalogEvent := "B1_confirmed"
	catalogAlgorithmID := "ALG-SIG-001"
	lowerTurnID := "segment-turn-1"
	signals := []ChanSignalPoint{{ObjectID: "signal-1", BarIndex: 20, Time: 2000, PriceI64: 90, SignalType: "buy_1", Status: "confirmed", LevelID: &levelID, LowerLevelTurnObjectID: &lowerTurnID, CatalogEvent: &catalogEvent, CatalogAlgorithmID: &catalogAlgorithmID, Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 20, ObjectRevision: 1}}
	breakoutWarning := "rising_wedge_below_b"
	monitors := []ChanCenterMonitor{{ObjectID: "monitor-1", BarIndex: 20, Time: 2000, ZI64: 100, ZnI64: 101, ZTwiceI64: 201, ZnTwiceI64: 203, CoreLowI64: 90, CoreHighI64: 111, RangeHighI64: 113, RangeLowI64: 90, ComponentOrdinal: 3, ComponentDirection: "up", RelativePosition: "above", OscillationBias: "strong", BreakoutWarning: &breakoutWarning, CatalogAlgorithmID: "ALG-AUX-004", SemanticNamespace: "auxiliary", EvidenceLevel: "AUXILIARY", LevelMappingProfile: "segment_center_components_v1", StandardSignal: false, ExecutionAllowed: false, ConfirmsThirdPoint: false, AnalysisLevel: "segment", ReferenceObjectID: "zhongshu-1", Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 20, ObjectRevision: 1}}
	levelCenters := []ChanLevelCenter{{ObjectID: "level-center-1", LevelID: "L1", ParentLevelID: "L0", StartBarIndex: 18, StartTime: 1800, EndBarIndex: 30, EndTime: 3000, ZDI64: 95, ZGI64: 105, DDI64: 90, GGI64: 110, ComponentKind: "segment", ComponentObjectIDs: []string{"segment-1"}, SourceCenterIDs: []string{"zhongshu-1"}, Status: "promoted", PromotionReason: "nine_component_extension", CatalogEvent: "center_promoted", CatalogAlgorithmID: "ALG-GEO-005", Confirmed: true, ConfirmedAtBarIndex: &confirmedAt, KnownAtBarIndex: 20, ObjectRevision: 1}}
	levelMovements := []ChanLevelMovement{{ObjectID: "level-movement-1", LevelID: "L0", StartBarIndex: 18, StartTime: 1800, EndBarIndex: 30, EndTime: 3000, LowI64: 90, HighI64: 110, ComponentCenterIDs: []string{"zhongshu-1"}, Classification: "consolidation", Status: "candidate", CatalogEvent: "movement_candidate", CatalogAlgorithmID: "ALG-GEO-006", KnownAtBarIndex: 20, ObjectRevision: 1}}
	for name, value := range map[string]any{"processed_bars.parquet": processedBars, "fractals.parquet": fractals, "bi.parquet": lines, "bi_states.parquet": biStates, "segments.parquet": lines, "zhongshu.parquet": centres, "segment_zhongshu.parquet": centres, "level_centers.parquet": levelCenters, "level_movements.parquet": levelMovements, "movement_states.parquet": []ChanMovementState{}, "center_monitors.parquet": monitors, "divergences.parquet": signals, "trade_points.parquet": signals, "events.parquet": []chanEventTestRow{}} {
		var err error
		switch rows := value.(type) {
		case []ChanProcessedBar:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanFractal:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanLineObject:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanZhongshu:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanSignalPoint:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanMovementState:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanCenterMonitor:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanBiState:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanLevelCenter:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []ChanLevelMovement:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		case []chanEventTestRow:
			err = parquet.WriteFile(filepath.Join(directory, name), rows)
		}
		if err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}
	if err := os.WriteFile(filepath.Join(directory, "_SUCCESS"), nil, 0o640); err != nil {
		t.Fatal(err)
	}
	request := Request{DatasetID: meta.DatasetID, DataRevision: meta.DataRevision, Algorithm: testChanAlgorithm, Parameters: map[string]any{}, CalculationMode: "causal_events"}
	created, err := service.Submit(context.Background(), "request", "trace", request)
	if err != nil || !created.CacheHit {
		t.Fatalf("expected Chan cache hit: %#v %v", created, err)
	}
	result, err := service.Results(created.Job.ID, 15, 25)
	if err != nil {
		t.Fatal(err)
	}
	if result.ResultKind != "chan" || result.Objects == nil || len(result.Objects.ProcessedBars) != 1 || len(result.Objects.Fractals) != 0 || len(result.Objects.Bi) != 1 || len(result.Objects.BiStates) != 1 || len(result.Objects.Segments) != 1 || len(result.Objects.Zhongshu) != 1 || len(result.Objects.SegmentZhongshu) != 1 || len(result.Objects.LevelCenters) != 1 || len(result.Objects.LevelMovements) != 1 || len(result.Objects.CenterMonitors) != 1 || len(result.Objects.Divergences) != 1 || len(result.Objects.TradePoints) != 1 || result.Coverage.ReturnedCount != 11 {
		t.Fatalf("unexpected Chan range result: %#v", result)
	}
	if result.Objects.TradePoints[0].Status != "confirmed" || result.Objects.TradePoints[0].LowerLevelTurnObjectID == nil || *result.Objects.TradePoints[0].LowerLevelTurnObjectID != lowerTurnID {
		t.Fatalf("unexpected first-point lifecycle result: %#v", result.Objects.TradePoints[0])
	}
	if result.Objects.Bi[0].StartExtremeSourceBarIndex != 10 || result.Objects.Bi[0].EndExtremeSourceBarIndex != 20 {
		t.Fatalf("unexpected Chan extreme source indexes: %#v", result.Objects.Bi[0])
	}
	if len(result.Objects.ProcessedBars[0].SourceBarIndices) != 3 || result.Objects.BiStates[0].State != "DOWN_EXTENDING" {
		t.Fatalf("online geometry lifecycle was not preserved: %#v %#v", result.Objects.ProcessedBars[0], result.Objects.BiStates[0])
	}
	if result.Objects.SegmentZhongshu[0].ZDI64 != result.Objects.SegmentZhongshu[0].ZGI64 {
		t.Fatalf("point center boundary was not preserved: %#v", result.Objects.SegmentZhongshu[0])
	}
	monitor := result.Objects.CenterMonitors[0]
	if monitor.ZTwiceI64 != 201 || monitor.ZnTwiceI64 != 203 || monitor.ComponentOrdinal != 3 || monitor.OscillationBias != "strong" || monitor.BreakoutWarning == nil || *monitor.BreakoutWarning != breakoutWarning || monitor.StandardSignal || monitor.ExecutionAllowed || monitor.ConfirmsThirdPoint {
		t.Fatalf("Zn monitor contract was not preserved: %#v", monitor)
	}
	fractalResult, err := service.Results(created.Job.ID, 0, 14)
	if err != nil || fractalResult.Objects == nil || len(fractalResult.Objects.Fractals) != 1 {
		t.Fatalf("unexpected fractal range result: %#v %v", fractalResult, err)
	}
	if fractalResult.Objects.Fractals[0].ZoneLowI64 != 100 || fractalResult.Objects.Fractals[0].ZoneHighI64 != 110 {
		t.Fatalf("fractal zone was not preserved: %#v", fractalResult.Objects.Fractals[0])
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
