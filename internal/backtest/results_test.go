package backtest

import (
	"path/filepath"
	"testing"

	"github.com/parquet-go/parquet-go"
)

func TestReadParquetRowsReturnsEmptySliceForSchemaOnlyFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trades.parquet")
	if err := parquet.WriteFile(path, []Trade{}); err != nil {
		t.Fatal(err)
	}
	rows, err := readParquetRows[Trade](path)
	if err != nil {
		t.Fatalf("read schema-only trades: %v", err)
	}
	if rows == nil || len(rows) != 0 {
		t.Fatalf("expected a non-nil empty result, got %#v", rows)
	}
}
