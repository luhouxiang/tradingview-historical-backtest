package logx_test

import (
	"bytes"
	"os"
	"path/filepath"
	"regexp"
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
	root, _ := filepath.Abs("../..")
	logger, err := logx.New(logx.Options{Service: "test", Writer: &output, ProjectRoot: root})
	if err != nil {
		t.Fatal(err)
	}
	logger.Info("test.event", "hello", nil)
	line := strings.TrimSpace(output.String())
	pattern := regexp.MustCompile(`^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]\[INFO\]\[internal/logx/logx_test.go\]\[\d{3,}\] test\.event hello$`)
	if !pattern.MatchString(line) {
		t.Fatalf("log line = %q", line)
	}
}

func TestMultilineMessagesRepeatTheSamePrefix(t *testing.T) {
	var output bytes.Buffer
	logger, err := logx.New(logx.Options{Service: "test", Writer: &output})
	if err != nil {
		t.Fatal(err)
	}
	logger.Error("test.multiline", "first\nsecond", nil)
	lines := strings.Split(strings.TrimSpace(output.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("line count = %d", len(lines))
	}
	prefix := regexp.MustCompile(`^(\[[^\]]+\]\[ERROR\]\[logx_test.go\]\[\d{3,}\] )`)
	first := prefix.FindString(lines[0])
	second := prefix.FindString(lines[1])
	if first == "" || first != second {
		t.Fatalf("prefixes differ: %q %q", lines[0], lines[1])
	}
	if !strings.HasSuffix(lines[0], "test.multiline first") || !strings.HasSuffix(lines[1], "second") {
		t.Fatalf("unexpected multiline output: %q", lines)
	}
}

func TestFieldsAreAppendedToTextMessage(t *testing.T) {
	var output bytes.Buffer
	logger, err := logx.New(logx.Options{Service: "test", Writer: &output})
	if err != nil {
		t.Fatal(err)
	}
	logger.Info("test.event", "hello", map[string]any{"trace_id": "trace-1", "custom": "value"})
	line := strings.TrimSpace(output.String())
	if !strings.Contains(line, `test.event hello`) ||
		!strings.Contains(line, `"trace_id":"trace-1"`) ||
		!strings.Contains(line, `"custom":"value"`) {
		t.Fatalf("log line = %q", line)
	}
}

func TestConsoleWriterReceivesSameFixedTextOutput(t *testing.T) {
	var fileOutput bytes.Buffer
	var consoleOutput bytes.Buffer
	logger, err := logx.New(logx.Options{
		Service:       "test",
		Writer:        &fileOutput,
		ConsoleWriter: &consoleOutput,
	})
	if err != nil {
		t.Fatal(err)
	}
	logger.Info("test.console", "visible on screen", map[string]any{"case": "debug"})

	fileLine := strings.TrimSpace(fileOutput.String())
	consoleLine := strings.TrimSpace(consoleOutput.String())
	if fileLine == "" || fileLine != consoleLine {
		t.Fatalf("file line = %q, console line = %q", fileLine, consoleLine)
	}
	if !strings.Contains(consoleLine, `test.console visible on screen`) ||
		!strings.Contains(consoleLine, `"case":"debug"`) {
		t.Fatalf("console line = %q", consoleLine)
	}
}

func TestExternalClientLogUsesClientSource(t *testing.T) {
	var output bytes.Buffer
	logger, err := logx.New(logx.Options{Service: "vue-client", Writer: &output})
	if err != nil {
		t.Fatal(err)
	}
	logger.External(map[string]any{
		"level":           "INFO",
		"event":           "app.started",
		"message":         "ready",
		"source_file":     "src/main.ts",
		"source_line":     float64(18),
		"source_function": "bootstrap",
		"service":         "spoofed",
	})
	line := strings.TrimSpace(output.String())
	if !strings.Contains(line, "[INFO][src/main.ts][018] app.started ready") {
		t.Fatalf("log line = %q", line)
	}
}

func TestRotationCompressesAndCapsBackups(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "app.log")
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
