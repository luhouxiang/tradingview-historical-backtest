package logx_test

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
)

type discardWriter struct{}

func (discardWriter) Write(value []byte) (int, error) { return len(value), nil }

func BenchmarkStructuredLogDiscard(b *testing.B) {
	logger, err := logx.New(logx.Options{Service: "benchmark", Writer: discardWriter{}})
	if err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for index := range b.N {
		logger.Info("benchmark.event", "benchmark event", map[string]any{"sequence": index, "trace_id": "trace-benchmark"})
	}
}

func TestSourcePointsAtBusinessCaller(t *testing.T) {
	var output bytes.Buffer
	logger, err := logx.New(logx.Options{Service: "test", Writer: &output})
	if err != nil {
		t.Fatal(err)
	}
	logger.Info("test.event", "hello", nil)
	var event map[string]any
	if err := json.Unmarshal(output.Bytes(), &event); err != nil {
		t.Fatal(err)
	}
	file, _ := event["source_file"].(string)
	if !strings.HasSuffix(file, "logx_test.go") {
		t.Fatalf("source_file = %q, want business test file", file)
	}
	if event["source_line"].(float64) <= 0 {
		t.Fatal("source_line must be positive")
	}
}

func TestContextFieldsAreTopLevel(t *testing.T) {
	var output bytes.Buffer
	logger, err := logx.New(logx.Options{Service: "test", Writer: &output})
	if err != nil {
		t.Fatal(err)
	}
	logger.Info("test.event", "hello", map[string]any{"trace_id": "trace-1", "custom": "value"})
	var event map[string]any
	if err := json.Unmarshal(output.Bytes(), &event); err != nil {
		t.Fatal(err)
	}
	if event["trace_id"] != "trace-1" {
		t.Fatalf("trace_id = %v", event["trace_id"])
	}
	if event["fields"].(map[string]any)["custom"] != "value" {
		t.Fatalf("fields = %v", event["fields"])
	}
}

func TestRotationCompressesAndCapsBackups(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "app.ndjson")
	logger, err := logx.New(logx.Options{Service: "test", Path: path, MaxSizeMB: 1, MaxBackups: 9, Compress: true})
	if err != nil {
		t.Fatal(err)
	}
	payload := strings.Repeat("x", 32*1024)
	for index := 0; index < 400; index++ {
		logger.Info("test.rotation", payload, map[string]any{"sequence": index})
	}
	if err := logger.Close(); err != nil {
		t.Fatal(err)
	}
	var entries []os.DirEntry
	deadline := time.Now().Add(3 * time.Second)
	for {
		entries, err = os.ReadDir(dir)
		if err != nil {
			t.Fatal(err)
		}
		if len(entries) <= 10 || time.Now().After(deadline) {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if len(entries) > 10 {
		t.Fatalf("log file count = %d, want <= 10", len(entries))
	}
	foundGzip := false
	for _, entry := range entries {
		foundGzip = foundGzip || strings.HasSuffix(entry.Name(), ".gz")
	}
	if !foundGzip {
		t.Fatal("expected a compressed rotated backup")
	}
}
