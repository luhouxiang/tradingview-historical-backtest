package backtest

import (
	"errors"
	"testing"

	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type rankingCatalog struct {
	values map[string]catalog.DatasetMeta
}

func (value rankingCatalog) Get(datasetID, _ string) (catalog.DatasetMeta, error) {
	meta, ok := value.values[datasetID]
	if !ok {
		return catalog.DatasetMeta{}, catalog.ErrNotFound
	}
	return meta, nil
}

func TestRunSignatureIsReproducibleAndCoversExecutionFacts(t *testing.T) {
	request := Request{
		DataRevision: "sha256:" + repeat("1", 64),
		Strategy:     pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "example", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)},
		Parameters:   map[string]any{"period": int64(20)},
		Range:        Range{WarmupFromBarIndex: 0, FromBarIndex: 100, ToBarIndex: 200},
		Execution:    map[string]any{"signal_timing": "bar_close", "fill_timing": "next_bar_open", "commission": map[string]any{"mode": "fixed_per_contract", "amount_i64": 3}, "slippage": map[string]any{"mode": "ticks", "value": 1}, "contract_multiplier": 20, "margin_ratio": .1},
		Capital:      map[string]any{"initial_cash_i64": int64(1000), "currency": "CNY", "money_scale": int64(100)}, RandomSeed: 7,
	}
	base, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	equal, _ := Signature(request, "engine-1")
	if base != equal {
		t.Fatal("same facts produced different run signatures")
	}
	request.Execution["fill_timing"] = "bar_close"
	changed, _ := Signature(request, "engine-1")
	if changed == base {
		t.Fatal("execution timing did not change run signature")
	}
}

func TestRunSignatureCoversRankingContext(t *testing.T) {
	request := Request{
		DatasetID: "A.1d", DataRevision: "sha256:" + repeat("1", 64),
		Strategy:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "aux_ma_sector_rotation", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)},
		Parameters:     map[string]any{"ma_period_1": int64(5)},
		RankingContext: validTestRankingContext(),
		Range:          Range{WarmupFromBarIndex: 0, FromBarIndex: 100, ToBarIndex: 200},
		Execution:      map[string]any{}, Capital: map[string]any{}, RandomSeed: 7,
	}
	base, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	request.RankingContext.Memberships[1].SectorID = "sector-b"
	changed, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	if changed == base {
		t.Fatal("point-in-time sector membership did not change run signature")
	}
}

func TestRunSignatureCoversRiskOverlayContext(t *testing.T) {
	request := Request{
		DatasetID: "A.5m", DataRevision: "sha256:" + repeat("1", 64),
		Strategy:    pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "example", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)},
		Parameters:  map[string]any{"period": int64(20)},
		RiskOverlay: validTestRiskOverlay(),
		Range:       Range{WarmupFromBarIndex: 0, FromBarIndex: 100, ToBarIndex: 200},
		Execution:   map[string]any{}, Capital: map[string]any{}, RandomSeed: 7,
	}
	base, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	request.RiskOverlay.Context.Observations[0].EventRiskActive = true
	changed, err := Signature(request, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	if changed == base {
		t.Fatal("point-in-time risk market state did not change run signature")
	}
}

func TestNormalizeRiskOverlayValidatesAlgorithmParametersAndCausality(t *testing.T) {
	definition := testRiskDefinition()
	overlay := validTestRiskOverlay()
	normalized, err := NormalizeRiskOverlay([]pythonclient.AlgorithmDefinition{definition}, overlay, "sha256:"+repeat("1", 64), 0, 200)
	if err != nil {
		t.Fatal(err)
	}
	if normalized == nil || normalized.Parameters["max_position_weight_ppm"] != int64(100_000) {
		t.Fatalf("risk parameters were not normalized: %#v", normalized)
	}
	// An unhandled branch is a valid auditable input; Python must emit a block.
	normalized.Context.LegalFutureBranches = []string{"continue", "S3"}
	normalized.Context.HandledFutureBranches = []string{"continue"}
	if _, err := NormalizeRiskOverlay([]pythonclient.AlgorithmDefinition{definition}, normalized, "sha256:"+repeat("1", 64), 0, 200); err != nil {
		t.Fatalf("unhandled legal branch should be evaluated by the risk engine: %v", err)
	}

	invalid := validTestRiskOverlay()
	invalid.Context.Observations[0].AvailableAtBarIndex = 11
	if _, err := NormalizeRiskOverlay([]pythonclient.AlgorithmDefinition{definition}, invalid, "sha256:"+repeat("1", 64), 0, 200); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("future-known market state was accepted: %v", err)
	}
	invalid = validTestRiskOverlay()
	invalid.Parameters["leverage_allowed"] = true
	if _, err := NormalizeRiskOverlay([]pythonclient.AlgorithmDefinition{definition}, invalid, "sha256:"+repeat("1", 64), 0, 200); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("unapproved leverage was accepted: %v", err)
	}
}

func TestResolveRankingDatasetsBuildsSortedGuardedDailyReferences(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	metaA := rankingMeta("A.1d", "1", "datasets/A/revisions/one/bars.parquet")
	metaB := rankingMeta("B.1d", "2", "datasets/B/revisions/two/bars.parquet")
	service := NewService(guard, rankingCatalog{values: map[string]catalog.DatasetMeta{"A.1d": metaA, "B.1d": metaB}}, nil, nil, "1", 0)
	request := Request{
		DatasetID: "A.1d", DataRevision: metaA.DataRevision,
		Strategy:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "aux_ma_sector_rotation"},
		RankingContext: validTestRankingContext(),
	}
	refs, err := service.resolveRankingDatasets(request, metaA)
	if err != nil {
		t.Fatal(err)
	}
	if len(refs) != 2 || refs[0].DatasetID != "A.1d" || refs[1].DatasetID != "B.1d" {
		t.Fatalf("unexpected ranking refs: %#v", refs)
	}
	if refs[1].BarsPath != "datasets/B/revisions/two/bars.parquet" || refs[1].MetaPath != "datasets/B/revisions/two/meta.json" {
		t.Fatalf("unexpected explicit member paths: %#v", refs[1])
	}
}

func TestResolveRankingDatasetsRejectsContextLeakAndIncompatibleMetadata(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	metaA := rankingMeta("A.1d", "1", "datasets/A/revisions/one/bars.parquet")
	metaB := rankingMeta("B.1d", "2", "datasets/B/revisions/two/bars.parquet")
	service := NewService(guard, rankingCatalog{values: map[string]catalog.DatasetMeta{"A.1d": metaA, "B.1d": metaB}}, nil, nil, "1", 0)
	request := Request{
		DatasetID: "A.1d", DataRevision: metaA.DataRevision,
		Strategy:       pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "ordinary"},
		RankingContext: validTestRankingContext(),
	}
	if _, err := service.resolveRankingDatasets(request, metaA); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("non-ranking strategy accepted ranking context: %v", err)
	}
	request.Strategy.AlgorithmID = "aux_ma_sector_rotation"
	metaB.Timeframe = "30m"
	service.catalog = rankingCatalog{values: map[string]catalog.DatasetMeta{"A.1d": metaA, "B.1d": metaB}}
	if _, err := service.resolveRankingDatasets(request, metaA); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("ranking accepted incompatible member timeframe: %v", err)
	}
}

func TestValidRankingContextRejectsOverlappingMembershipIntervals(t *testing.T) {
	context := validTestRankingContext()
	end := int64(20)
	context.Memberships[0].EffectiveToUTC = &end
	context.Memberships = append(context.Memberships, RankingMembership{
		DatasetID: "A.1d", DataRevision: context.Memberships[0].DataRevision, SectorID: "sector-b",
		EffectiveFromUTC: 19, EffectiveToUTC: nil, AvailableAtUTC: 0,
	})
	if validRankingContext(*context, "A.1d", context.Memberships[0].DataRevision) {
		t.Fatal("overlapping point-in-time memberships were accepted")
	}
}

func validTestRankingContext() *RankingContext {
	return &RankingContext{
		UniverseID: "sample", MembershipRevision: "sha256:" + repeat("3", 64), MembershipMode: "point_in_time",
		PriceAdjustmentMode: "forward_adjusted", PriceAdjustmentRevision: "sha256:" + repeat("4", 64),
		EpisodeID: "episode", EpisodeStartTimestampUTC: 0, EpisodeAvailableAtUTC: 9,
		Memberships: []RankingMembership{
			{DatasetID: "A.1d", DataRevision: "sha256:" + repeat("1", 64), SectorID: "sector-a", EffectiveFromUTC: 0, AvailableAtUTC: 0},
			{DatasetID: "B.1d", DataRevision: "sha256:" + repeat("2", 64), SectorID: "sector-a", EffectiveFromUTC: 0, AvailableAtUTC: 0},
		},
	}
}

func validTestRiskOverlay() *RiskOverlay {
	return &RiskOverlay{
		Algorithm: pythonclient.AlgorithmRef{
			Kind: "risk_filter", AlgorithmID: "unified_risk_execution_overlay", AlgorithmVersion: "1.0.0",
			SourceHash: "sha256:" + repeat("f", 64),
		},
		Parameters: map[string]any{},
		Context: RiskContext{
			MarketStateRevision: "sha256:" + repeat("3", 64), SectorID: "metals",
			LegalFutureBranches: []string{"continue"}, HandledFutureBranches: []string{"continue"},
			Observations: []RiskMarketObservation{{
				EffectiveFromBarIndex: 10, AvailableAtBarIndex: 10,
				DataRevision: "sha256:" + repeat("1", 64), TradingStatus: "normal",
			}},
		},
	}
}

func testRiskDefinition() pythonclient.AlgorithmDefinition {
	properties := map[string]any{
		"leverage_allowed":                   map[string]any{"type": "boolean", "default": false},
		"leverage_approval_id":               map[string]any{"type": "string", "default": ""},
		"max_position_weight_ppm":            map[string]any{"type": "integer", "minimum": 1, "maximum": 5_000_000, "default": 100_000},
		"max_sector_weight_ppm":              map[string]any{"type": "integer", "minimum": 1, "maximum": 5_000_000, "default": 300_000},
		"max_order_loss_weight_ppm":          map[string]any{"type": "integer", "minimum": 1, "maximum": 1_000_000, "default": 10_000},
		"stress_loss_per_contract_i64":       map[string]any{"type": "integer", "minimum": 1, "maximum": int64(9_223_372_036_854_775_807), "default": 100_000},
		"max_daily_loss_ppm":                 map[string]any{"type": "integer", "minimum": 1, "maximum": 1_000_000, "default": 20_000},
		"max_strategy_drawdown_ppm":          map[string]any{"type": "integer", "minimum": 1, "maximum": 1_000_000, "default": 150_000},
		"max_order_participation_ppm":        map[string]any{"type": "integer", "minimum": 1, "maximum": 1_000_000, "default": 100_000},
		"max_stale_bars":                     map[string]any{"type": "integer", "minimum": 0, "maximum": 10_000, "default": 0},
		"max_data_gap_bars":                  map[string]any{"type": "integer", "minimum": 0, "maximum": 10_000, "default": 0},
		"max_open_signal_age_bars":           map[string]any{"type": "integer", "minimum": 0, "maximum": 10_000, "default": 3},
		"event_risk_max_position_weight_ppm": map[string]any{"type": "integer", "minimum": 0, "maximum": 5_000_000, "default": 50_000},
		"kill_switch_on_data_revision":       map[string]any{"type": "boolean", "default": true},
	}
	overlay := validTestRiskOverlay()
	return pythonclient.AlgorithmDefinition{
		AlgorithmRef:    overlay.Algorithm,
		ParameterSchema: map[string]any{"properties": properties},
	}
}

func rankingMeta(datasetID, revisionDigit, barsPath string) catalog.DatasetMeta {
	return catalog.DatasetMeta{
		DatasetID: datasetID, DataRevision: "sha256:" + repeat(revisionDigit, 64), Timeframe: "1d",
		Source: catalog.SourceMeta{TimestampSemantics: "bar_end"},
		Time:   catalog.TimeMeta{DateSemantics: "trading_day", Timezone: "Asia/Shanghai"},
		Files:  []catalog.FileMeta{{Role: "bars", Path: barsPath}},
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
