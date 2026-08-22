package workspace

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func testLayout() Layout {
	return Layout{
		SchemaVersion: 1, ProfileID: "default", LayoutID: "layout-1", Revision: 1,
		Panes:       []Pane{{ID: "main", Role: "price", Weight: 6, MinHeight: 240, Visible: true}},
		RightPanel:  Panel{Width: 320, ActiveTab: "object_tree"},
		BottomPanel: Panel{Height: 260, Collapsed: true, ActiveTab: "tasks"},
		ObjectOrder: []ObjectOrder{}, SeriesSources: []SeriesSource{}, StrategySources: []StrategySource{},
	}
}

func TestLayoutSemanticValidation(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	store := NewStore(guard)
	tests := []struct {
		name   string
		mutate func(*Layout)
	}{
		{"missing price pane", func(layout *Layout) { layout.Panes[0].Role = "indicator" }},
		{"pixel-sized side panel", func(layout *Layout) { layout.RightPanel.Width = 200 }},
		{"duplicate pane", func(layout *Layout) { layout.Panes = append(layout.Panes, layout.Panes[0]) }},
		{"invalid layer", func(layout *Layout) { layout.ObjectOrder = []ObjectOrder{{ID: "one", PaneID: "main", ZBand: 42}} }},
		{"invalid source revision", func(layout *Layout) {
			layout.SeriesSources = []SeriesSource{{
				SourceID: "ma-1", Name: "MA", PaneID: "main", ZBand: 400, DatasetID: "SHFE.AO2609.5m", DataRevision: "bad",
				Algorithm: pythonclient.AlgorithmRef{Kind: "indicator", AlgorithmID: "ma", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("1", 64)}, Parameters: map[string]any{},
			}}
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			layout := testLayout()
			test.mutate(&layout)
			if _, err := store.PutLayout("default", "layout-1", 0, layout); !errors.Is(err, ErrInvalid) {
				t.Fatalf("expected invalid layout, got %v", err)
			}
		})
	}
}

func testDrawings() Drawings {
	return Drawings{
		SchemaVersion: 1, ProfileID: "default", LayoutID: "layout-1", DatasetID: "SHFE.AO2609.5m",
		DataRevision: "sha256:" + repeat("1", 64), Drawings: []Drawing{{
			ID: "drawing-1", Name: "矩形 1", Type: "rectangle", PaneID: "main", Visible: true,
			ZBand: 600, Style: DrawingStyle{Color: "#2962ff", LineWidth: 1, FillOpacity: .15},
			Anchors: []Anchor{{Time: 1000, PriceI64: 200, PriceScale: 10}, {Time: 2000, PriceI64: 300, PriceScale: 10}}, Revision: 1,
		}},
	}
}

func TestAtomicSaveReadAndRevisionConflict(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	store := NewStore(guard)
	saved, err := store.PutLayout("default", "layout-1", 0, testLayout())
	if err != nil || saved.Revision != 1 || saved.UpdatedAt.IsZero() {
		t.Fatalf("save layout: %#v %v", saved, err)
	}
	read, err := store.GetLayout("default", "layout-1")
	if err != nil || read.Revision != 1 {
		t.Fatalf("read layout: %#v %v", read, err)
	}
	_, err = store.PutLayout("default", "layout-1", 0, testLayout())
	var conflict *ConflictError
	if !errors.As(err, &conflict) || conflict.CurrentRevision != 1 {
		t.Fatalf("expected current revision 1, got %v", err)
	}
	files, err := filepath.Glob(filepath.Join(guard.Root(), "workspaces/default/layouts/.tmp-*"))
	if err != nil || len(files) != 0 {
		t.Fatalf("temporary files remained: %v %v", files, err)
	}
}

func TestIndicatorStylePersistenceAndValidation(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	store := NewStore(guard)
	style := &IndicatorStyle{Outputs: map[string]IndicatorOutputStyle{
		"ma": {Color: "#ab47bc", LineWidth: 3, LineStyle: "dashed", Opacity: 0.7, Visible: true},
	}}
	layout := testLayout()
	layout.SeriesSources = []SeriesSource{{
		SourceID: "ma-1", Name: "MA", PaneID: "main", Visible: true, ZBand: 400,
		DatasetID: "SHFE.AO2609.5m", DataRevision: "sha256:" + repeat("1", 64),
		Algorithm:  pythonclient.AlgorithmRef{Kind: "indicator", AlgorithmID: "ma", AlgorithmVersion: "1.0.0", SourceHash: "sha256:" + repeat("2", 64)},
		Parameters: map[string]any{"period": 20}, Style: style,
	}}
	saved, err := store.PutLayout("default", "layout-1", 0, layout)
	if err != nil || saved.SeriesSources[0].Style.Outputs["ma"].LineWidth != 3 {
		t.Fatalf("save indicator style: %#v %v", saved.SeriesSources, err)
	}
	read, err := store.GetLayout("default", "layout-1")
	if err != nil || read.SeriesSources[0].Style.Outputs["ma"].LineStyle != "dashed" {
		t.Fatalf("read indicator style: %#v %v", read.SeriesSources, err)
	}

	invalid := testLayout()
	invalid.SeriesSources = layout.SeriesSources
	invalid.SeriesSources[0].Style = &IndicatorStyle{Outputs: map[string]IndicatorOutputStyle{
		"ma": {Color: "purple", LineWidth: 8, LineStyle: "wave", Opacity: 2, Visible: true},
	}}
	if _, err := store.PutLayout("default", "invalid-style", 0, invalid); !errors.Is(err, ErrInvalid) {
		t.Fatalf("expected invalid indicator style, got %v", err)
	}
}

func TestStrategySourceConfigUsesDedicatedAtomicFile(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	store := NewStore(guard)
	document := StrategySourceConfig{
		SchemaVersion: 1, ProfileID: "default", Revision: 1,
		StrategySources: []StrategySourcePreference{{
			DatasetID: "SHFE.AO2609.5m", DataRevision: "sha256:" + repeat("1", 64), SourceID: "strategy-default-chan", Visible: true,
			CategoryVisibility: DynamicCategoryVisibility{ProcessedBars: false, Bi: true, BiStates: true, Segments: true, Zhongshu: true, SegmentZhongshu: true, MovementStates: true, CenterMonitors: true, Divergences: true, TradePoints: true},
		}},
	}
	saved, err := store.PutStrategySourceConfig("default", 0, document)
	if err != nil || saved.Revision != 1 || saved.UpdatedAt.IsZero() {
		t.Fatalf("save strategy source config: %#v %v", saved, err)
	}
	path, _ := guard.Resolve("workspaces/default/strategy-source-config.json")
	data, err := os.ReadFile(path)
	if err != nil || !contains(string(data), `"strategy_sources"`) || contains(string(data), `"panes"`) {
		t.Fatalf("dedicated dynamic config is invalid: %s %v", data, err)
	}
	read, err := store.GetStrategySourceConfig("default")
	if err != nil || !read.StrategySources[0].CategoryVisibility.Bi || !read.StrategySources[0].CategoryVisibility.BiStates {
		t.Fatalf("read strategy source config: %#v %v", read, err)
	}
	_, err = store.PutStrategySourceConfig("default", 0, document)
	var conflict *ConflictError
	if !errors.As(err, &conflict) || conflict.CurrentRevision != 1 {
		t.Fatalf("expected current revision 1, got %v", err)
	}
}

func TestDrawingSaveUsesAnchorsAndRejectsEscapes(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	store := NewStore(guard)
	saved, err := store.PutDrawings("default", "layout-1", "SHFE.AO2609.5m", 0, testDrawings())
	if err != nil || saved.Revision != 1 || saved.Drawings[0].Anchors[0].PriceI64 != 200 {
		t.Fatalf("save drawings: %#v %v", saved, err)
	}
	path, _ := guard.Resolve("workspaces/default/drawings/layout-1/SHFE.AO2609.5m.json")
	data, _ := os.ReadFile(path)
	if string(data) == "" || contains(string(data), `"x"`) || contains(string(data), `"y"`) {
		t.Fatalf("drawing persistence contains pixels or is empty: %s", data)
	}
	if _, err := store.PutDrawings("../escape", "layout-1", "SHFE.AO2609.5m", 0, testDrawings()); !errors.Is(err, ErrInvalid) {
		t.Fatalf("path escape was not rejected: %v", err)
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}

func contains(value, part string) bool {
	for index := 0; index+len(part) <= len(value); index++ {
		if value[index:index+len(part)] == part {
			return true
		}
	}
	return false
}
