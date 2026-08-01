package replay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func TestReplayCacheKeyIncludesRangeAndStrategyFacts(t *testing.T) {
	strategy := pythonclient.AlgorithmRef{Kind: "chan", AlgorithmID: "chan", AlgorithmVersion: "1", SourceHash: "sha256:" + repeat("2", 64)}
	base, err := CacheKey("sha256:"+repeat("1", 64), strategy, map[string]any{"left": 2}, 100, 200, 0, "engine-1")
	if err != nil {
		t.Fatal(err)
	}
	variants := [][3]int64{{101, 200, 0}, {100, 201, 0}, {100, 200, 1}}
	for _, value := range variants {
		key, _ := CacheKey("sha256:"+repeat("1", 64), strategy, map[string]any{"left": 2}, value[0], value[1], value[2], "engine-1")
		if key == base {
			t.Fatalf("range variant %v did not invalidate replay cache", value)
		}
	}
}

func TestReadEventsFiltersKnownAtAndDecodesPayload(t *testing.T) {
	root := t.TempDir()
	guard, err := storage.NewPathGuard(root)
	if err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(root, "cache", "replay", "key")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	key := "sha256:" + repeat("3", 64)
	manifest, _ := json.Marshal(map[string]any{"cache_key": key})
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifest, 0o644); err != nil {
		t.Fatal(err)
	}
	rows := []eventRow{
		{1, 4, "fractal", "f-1", "upsert", 1, `{"price_i64":10}`},
		{2, 7, "bi", "b-1", "delete", 2, `{}`},
		{3, 9, "bi", "b-2", "upsert", 1, `{"direction":"up"}`},
	}
	if err := parquet.WriteFile(filepath.Join(directory, "events.parquet"), rows); err != nil {
		t.Fatal(err)
	}
	result, err := readEvents(guard, "replay-1", key, "cache/replay/key", 5, 8)
	if err != nil {
		t.Fatal(err)
	}
	if result.EventCount != 1 || result.Events[0].ObjectID != "b-1" || len(result.Events[0].Payload) != 0 {
		t.Fatalf("unexpected events: %#v", result.Events)
	}
	if len(result.Checksum) != 71 {
		t.Fatalf("invalid checksum: %s", result.Checksum)
	}
}

func repeat(value string, count int) string {
	result := ""
	for range count {
		result += value
	}
	return result
}
