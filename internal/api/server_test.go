package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/importer"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
	"github.com/tvbt/tradingview-historical-backtest/internal/marketdata"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
)

func testServer(t *testing.T, pythonURL string, options ...Option) (*Server, *bytes.Buffer) {
	t.Helper()
	cfg, err := config.Load(filepath.Join("..", "..", "config", "app.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var goOutput, vueOutput bytes.Buffer
	goLogger, _ := logx.New(logx.Options{Service: "go-api", Writer: &goOutput})
	vueLogger, _ := logx.New(logx.Options{Service: "vue-client", Writer: &vueOutput})
	return NewServer(cfg, pythonclient.New(pythonURL, cfg.App.ContractVersion, time.Second), goLogger, vueLogger, options...), &vueOutput
}

type fakeDatasets struct {
	mu      sync.Mutex
	sources []importer.SourceFile
	meta    catalog.DatasetMeta
	imports []importer.ImportRequest
}

func (f *fakeDatasets) Scan(_ context.Context) ([]importer.SourceFile, error) { return f.sources, nil }
func (f *fakeDatasets) SourceFiles() []importer.SourceFile                    { return f.sources }
func (f *fakeDatasets) Import(_ context.Context, request importer.ImportRequest, progress func(float64)) (catalog.DatasetMeta, bool, error) {
	f.mu.Lock()
	f.imports = append(f.imports, request)
	f.mu.Unlock()
	progress(1)
	return f.meta, false, nil
}

func (f *fakeDatasets) importCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.imports)
}

func TestDatasetBatchImportAndResearchReadiness(t *testing.T) {
	revision := "sha256:" + strings.Repeat("a", 64)
	data := &fakeDatasets{meta: catalog.DatasetMeta{
		DatasetID: "SHFE.AOL9.5m", DataRevision: revision, IndependenceGroup: "SHFE.AO", Timeframe: "5m",
		Instrument: catalog.InstrumentMeta{Exchange: "SHFE", Product: "AO", Symbol: "AOL9"},
		Coverage:   catalog.CoverageMeta{TradingDayCount: 600, FirstTimestampUTC: 1, LastTimestampUTC: 2},
	}}
	manager := jobs.NewManager()
	server, _ := testServer(t, "http://127.0.0.1:1", WithDatasets(data, manager))
	body := `{"items":[{"source_file_id":"source-1","importer_id":"tdx_txt_v1","exchange":"SHFE","instrument":"AOL9","timeframe":"5m","date_semantics":"trading_day","timezone":"Asia/Shanghai"},{"source_file_id":"source-2","importer_id":"tdx_txt_v1","exchange":"DCE","instrument":"YL9","timeframe":"5m","date_semantics":"trading_day","timezone":"Asia/Shanghai"}]}`
	request := httptest.NewRequest(http.MethodPost, "/api/v1/datasets/import-batch", strings.NewReader(body))
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("batch status = %d: %s", recorder.Code, recorder.Body.String())
	}
	deadline := time.Now().Add(time.Second)
	for data.importCount() != 2 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if data.importCount() != 2 {
		t.Fatalf("imports = %d, want 2", data.importCount())
	}
	request = httptest.NewRequest(http.MethodGet, "/api/v1/datasets/research-readiness", nil)
	recorder = httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"status":"exploratory"`) || !strings.Contains(recorder.Body.String(), `"eligible_independence_group_count":1`) {
		t.Fatalf("readiness response = %d: %s", recorder.Code, recorder.Body.String())
	}
}
func (f *fakeDatasets) ListDatasets() (catalog.Document, []catalog.DatasetMeta, error) {
	return catalog.Document{CatalogRevision: 1}, []catalog.DatasetMeta{f.meta}, nil
}
func (f *fakeDatasets) GetDataset(_, _ string) (catalog.DatasetMeta, error) { return f.meta, nil }

type fakeBarReader struct {
	query marketdata.Query
	err   error
}

func (f *fakeBarReader) Read(_ context.Context, query marketdata.Query) (marketdata.Response, error) {
	f.query = query
	if f.err != nil {
		return marketdata.Response{}, f.err
	}
	return marketdata.Response{
		DatasetID: query.DatasetID, DataRevision: query.DataRevision, GenerationID: query.GenerationID, PriceScale: 1,
		Coverage: marketdata.Coverage{FirstBarIndex: 7, LastBarIndex: 8}, Checksum: "sha256:" + strings.Repeat("a", 64),
		Bars: marketdata.BarColumns{BarIndex: []int64{7, 8}, TimestampUTC: []int64{10, 20}, OpenI64: []int64{1, 2}, HighI64: []int64{2, 3}, LowI64: []int64{0, 1}, CloseI64: []int64{1, 2}, Volume: []int64{3, 4}, OpenInterest: []*int64{nil, nil}},
	}, nil
}

func TestDatasetScanUsesPollableJob(t *testing.T) {
	data := &fakeDatasets{sources: []importer.SourceFile{{SourceFileID: "source-1", Path: "history/sample.txt", Status: "importable"}}}
	server, _ := testServer(t, "http://127.0.0.1:1", WithDatasets(data, jobs.NewManager()))
	request := httptest.NewRequest(http.MethodPost, "/api/v1/datasets/scan", nil)
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("scan status = %d", recorder.Code)
	}
	var accepted map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &accepted); err != nil {
		t.Fatal(err)
	}
	jobID := accepted["job_id"].(string)
	deadline := time.Now().Add(time.Second)
	for {
		request = httptest.NewRequest(http.MethodGet, "/api/v1/jobs/"+jobID, nil)
		recorder = httptest.NewRecorder()
		server.Handler().ServeHTTP(recorder, request)
		var status map[string]any
		if err := json.Unmarshal(recorder.Body.Bytes(), &status); err != nil {
			t.Fatal(err)
		}
		if status["status"] == "completed" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("job did not complete: %v", status)
		}
		time.Sleep(time.Millisecond)
	}
	request = httptest.NewRequest(http.MethodGet, "/api/v1/source-files", nil)
	recorder = httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), "source-1") {
		t.Fatalf("source files response: %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestGetBarsPassesCursorAndReturnsColumnarResponse(t *testing.T) {
	reader := &fakeBarReader{}
	server, _ := testServer(t, "http://127.0.0.1:1", WithBarReader(reader))
	revision := "sha256:" + strings.Repeat("b", 64)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/datasets/SHFE.AO2609.5m/bars?revision="+revision+"&generation_id=gen-42&before_bar_index=9&limit=2", nil)
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || reader.query.BeforeBarIndex == nil || *reader.query.BeforeBarIndex != 9 || reader.query.Limit != 2 {
		t.Fatalf("bar response %d, query %#v: %s", recorder.Code, reader.query, recorder.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["request_id"] == "" || payload["generation_id"] != "gen-42" {
		t.Fatalf("bar payload: %#v", payload)
	}
}

func TestGetBarsMapsStableErrors(t *testing.T) {
	tests := []struct {
		err    error
		status int
		code   string
	}{
		{marketdata.ErrInvalidRange, http.StatusBadRequest, "INVALID_BAR_QUERY"},
		{marketdata.ErrRevisionMismatch, http.StatusConflict, "DATA_REVISION_MISMATCH"},
		{catalog.ErrNotFound, http.StatusNotFound, "DATASET_NOT_FOUND"},
	}
	for _, test := range tests {
		reader := &fakeBarReader{err: test.err}
		server, _ := testServer(t, "http://127.0.0.1:1", WithBarReader(reader))
		request := httptest.NewRequest(http.MethodGet, "/api/v1/datasets/missing/bars?revision=sha256:"+strings.Repeat("a", 64)+"&generation_id=gen", nil)
		recorder := httptest.NewRecorder()
		server.Handler().ServeHTTP(recorder, request)
		if recorder.Code != test.status || !strings.Contains(recorder.Body.String(), test.code) {
			t.Fatalf("error %v => %d %s", test.err, recorder.Code, recorder.Body.String())
		}
	}
}

func TestHealthIncludesPythonState(t *testing.T) {
	python := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "contract_version": "1.0.0", "services": map[string]any{}})
	}))
	defer python.Close()
	server, _ := testServer(t, python.URL)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, req)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d", recorder.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	services := payload["services"].(map[string]any)
	if services["python-engine"].(map[string]any)["status"] != "ok" {
		t.Fatalf("unexpected payload: %v", payload)
	}
}

func TestClientLogsOverrideService(t *testing.T) {
	server, output := testServer(t, "http://127.0.0.1:1")
	body := `{"events":[{"timestamp":"2026-08-01T10:00:00Z","level":"INFO","service":"spoofed","event":"app.started","message":"started","source_file":"src/main.ts","source_line":10,"source_function":"bootstrap"}]}`
	req := httptest.NewRequest(http.MethodPost, "/api/v1/client-logs", bytes.NewBufferString(body))
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, req)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("status = %d, body=%s", recorder.Code, recorder.Body.String())
	}
	line := strings.TrimSpace(output.String())
	if !strings.Contains(line, "[INFO][src/main.ts][010] app.started started") {
		t.Fatalf("vue log line = %q", line)
	}
	if strings.Contains(line, "spoofed") {
		t.Fatalf("vue log line leaked client service field: %q", line)
	}
}
