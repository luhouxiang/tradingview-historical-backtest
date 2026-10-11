package calculation

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/parquet-go/parquet-go"
)

func TestRecursiveRowsPreserveIntegerProofAndRangeFilter(t *testing.T) {
	directory := t.TempDir()
	row := recursiveRow{ObjectID: "parent", Level: 2, StartBarIndex: 10, EndBarIndex: 30, KnownAtBarIndex: 40, ObjectRevision: 3,
		PayloadJSON: `{"object_id":"parent","level":2,"object_revision":3,"known_at_bar_index":40,"zd_i64":9007199254740993,"seed_refs":[{"object_id":"child","object_revision":2}]}`}
	if err := parquet.WriteFile(filepath.Join(directory, "recursive_centers.parquet"), []recursiveRow{row}); err != nil {
		t.Fatal(err)
	}
	rows, err := readRecursiveRows(directory, "recursive_centers", 20, 25)
	if err != nil || len(rows) != 1 {
		t.Fatalf("read rows: %v %v", rows, err)
	}
	if !json.Valid(rows[0]) || !strings.Contains(string(rows[0]), "9007199254740993") {
		t.Fatal("proof price lost integer precision")
	}
	rows, err = readRecursiveRows(directory, "recursive_centers", 31, 50)
	if err != nil || len(rows) != 0 {
		t.Fatal("range filter includes disjoint parent")
	}
	row.PayloadJSON = `{"object_id":"parent","level":2,"object_revision":2,"known_at_bar_index":40}`
	if err := parquet.WriteFile(filepath.Join(directory, "recursive_centers.parquet"), []recursiveRow{row}); err != nil {
		t.Fatal(err)
	}
	if _, err = readRecursiveRows(directory, "recursive_centers", 20, 25); err == nil {
		t.Fatal("mismatched revision accepted")
	}
}

func TestRecursiveActualCachePublicResultProjection(t *testing.T) {
	directory := os.Getenv("TVBT_RECURSIVE_CACHE")
	if directory == "" {
		t.Skip("requires an explicitly selected immutable v21 cache")
	}
	data, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var meta manifest
	if err = json.Unmarshal(data, &meta); err != nil {
		t.Fatal(err)
	}
	if meta.SchemaVersion != 13 || meta.Algorithm.AlgorithmVersion != "22.3.0" {
		t.Fatal("unexpected recursive cache identity")
	}
	parentFound := false
	for from := int64(0); from < 38376; from += 5000 {
		result, err := readChanResults(directory, "m17-read-only-audit", meta.CacheKey, meta, from, from+4999)
		if err != nil {
			t.Fatal(err)
		}
		if result.DataRevision != meta.DataRevision || result.Objects == nil {
			t.Fatal("lost dataset identity")
		}
		for _, raw := range result.Objects.RecursiveCenters {
			var center struct {
				Level int64 `json:"level"`
				ZD    int64 `json:"zd_i64"`
				ZG    int64 `json:"zg_i64"`
			}
			if err = json.Unmarshal(raw, &center); err != nil {
				t.Fatal(err)
			}
			if center.ZD > center.ZG {
				t.Fatal("invalid parent core")
			}
			parentFound = parentFound || center.Level >= 1
		}
	}
	if !parentFound {
		t.Fatal("actual higher-level center missing from paged result projection")
	}
}
