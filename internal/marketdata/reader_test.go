package marketdata

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/importer"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func TestReaderTailAndCursorRanges(t *testing.T) {
	reader, revision := testReader(t, 6000)
	tail, err := reader.Read(context.Background(), Query{DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen-1"})
	if err != nil {
		t.Fatal(err)
	}
	if len(tail.Bars.BarIndex) != 3000 || tail.Coverage.FirstBarIndex != 3000 || tail.Coverage.LastBarIndex != 5999 || !tail.HasMoreBefore {
		t.Fatalf("tail range: %#v (%d bars)", tail.Coverage, len(tail.Bars.BarIndex))
	}
	if tail.GenerationID != "gen-1" || tail.PriceScale != 10 || !strings.HasPrefix(tail.Checksum, "sha256:") {
		t.Fatalf("tail identity: %#v", tail)
	}
	before := int64(3000)
	prefetch, err := reader.Read(context.Background(), Query{
		DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen-1", BeforeBarIndex: &before,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(prefetch.Bars.BarIndex) != 1500 || prefetch.Bars.BarIndex[0] != 1500 || prefetch.Bars.BarIndex[1499] != 2999 || !prefetch.HasMoreBefore {
		t.Fatalf("prefetch range: %#v", prefetch)
	}
	before = 1000
	first, err := reader.Read(context.Background(), Query{
		DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen-1", BeforeBarIndex: &before, Limit: 1500,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Bars.BarIndex) != 1000 || first.Bars.BarIndex[0] != 0 || first.Bars.BarIndex[999] != 999 || first.HasMoreBefore {
		t.Fatalf("first range: %#v", first)
	}
	again, err := reader.Read(context.Background(), Query{DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen-2"})
	if err != nil || again.Checksum != tail.Checksum {
		t.Fatalf("stable checksum: %q/%q, %v", tail.Checksum, again.Checksum, err)
	}
}

func TestReaderRejectsRevisionAndInvalidRanges(t *testing.T) {
	reader, revision := testReader(t, 10)
	_, err := reader.Read(context.Background(), Query{
		DatasetID: "SHFE.AO2609.5m", DataRevision: "sha256:" + strings.Repeat("f", 64), GenerationID: "gen-1",
	})
	if !errors.Is(err, ErrRevisionMismatch) {
		t.Fatalf("revision mismatch = %v", err)
	}
	tail, before := 5, int64(5)
	for _, query := range []Query{
		{DatasetID: "SHFE.AO2609.5m", DataRevision: revision},
		{DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen", Tail: &tail, BeforeBarIndex: &before},
		{DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "gen", Limit: 10},
	} {
		if _, err := reader.Read(context.Background(), query); !errors.Is(err, ErrInvalidRange) {
			t.Fatalf("query %#v error = %v", query, err)
		}
	}
}

func BenchmarkReaderHotTail3000(b *testing.B) {
	reader, revision := testReader(b, 17_017)
	query := Query{DatasetID: "SHFE.AO2609.5m", DataRevision: revision, GenerationID: "benchmark"}
	if _, err := reader.Read(context.Background(), query); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for range b.N {
		result, err := reader.Read(context.Background(), query)
		if err != nil || len(result.Bars.BarIndex) != 3000 {
			b.Fatalf("hot read failed: %v", err)
		}
	}
}

func testReader(t testing.TB, count int) (*Reader, string) {
	t.Helper()
	root := t.TempDir()
	guard, err := storage.NewPathGuard(root)
	if err != nil {
		t.Fatal(err)
	}
	store, err := catalog.NewStore(guard)
	if err != nil {
		t.Fatal(err)
	}
	revision := "sha256:" + strings.Repeat("a", 64)
	relativeDir := "normalized/SHFE.AO2609.5m/aaaaaaaaaaaa"
	directory, err := guard.Resolve(relativeDir)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	bars := make([]importer.Bar, count)
	for index := range bars {
		openInterest := int64(index * 2)
		bars[index] = importer.Bar{
			BarIndex: int64(index), TimestampUTC: 1_700_000_000_000 + int64(index)*300_000,
			OpenI64: int64(index + 10), HighI64: int64(index + 12), LowI64: int64(index + 9), CloseI64: int64(index + 11),
			Volume: int64(index), OpenInterest: &openInterest,
		}
	}
	barsPath := filepath.Join(directory, "bars.parquet")
	if err := parquet.WriteFile(barsPath, bars); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "_SUCCESS"), []byte(revision+"\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	meta := catalog.DatasetMeta{
		SchemaVersion: 1, DatasetID: "SHFE.AO2609.5m", DataRevision: revision, Timeframe: "5m",
		Instrument: catalog.InstrumentMeta{Exchange: "SHFE", Symbol: "AO2609", Product: "AO"},
		Price:      catalog.PriceMeta{PriceScale: 10}, Coverage: catalog.CoverageMeta{BarCount: int64(count)},
		Files: []catalog.FileMeta{{Role: "bars", Path: filepath.ToSlash(filepath.Join(relativeDir, "bars.parquet"))}},
	}
	metaPath := filepath.ToSlash(filepath.Join(relativeDir, "meta.json"))
	metaBytes, err := json.Marshal(meta)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "meta.json"), metaBytes, 0o640); err != nil {
		t.Fatal(err)
	}
	if err := store.Upsert(meta, metaPath); err != nil {
		t.Fatal(err)
	}
	return NewReader(guard, store, Config{InitialBars: 3000, PrefetchBars: 1500, MaxBarsPerRequest: 5000}), revision
}
