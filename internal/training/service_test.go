package training

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type stubPrepare struct{ err error }

func (p *stubPrepare) Prepare(_ context.Context, _, _ string, r backtest.Request) (backtest.Request, catalog.DatasetMeta, error) {
	return r, catalog.DatasetMeta{DatasetID: r.DatasetID, DataRevision: r.DataRevision, Files: []catalog.FileMeta{{Role: "bars", Path: "normalized/test/bars.parquet"}}}, p.err
}

type stubPython struct {
	guard       *storage.PathGuard
	mu          sync.Mutex
	submissions []map[string]any
	release     chan struct{}
	submitted   chan struct{}
	remoteError map[string]any
}

func (p *stubPython) Algorithms(context.Context, string, string) ([]pythonclient.AlgorithmDefinition, error) {
	return nil, nil
}
func (p *stubPython) Health(context.Context) pythonclient.Health           { return pythonclient.Health{} }
func (p *stubPython) Cancel(context.Context, string, string, string) error { return nil }
func (p *stubPython) Submit(ctx context.Context, kind, _, _ string, value any) (pythonclient.JobStatus, error) {
	payload := value.(map[string]any)
	if kind != "training" {
		return pythonclient.JobStatus{}, errors.New("wrong kind")
	}
	p.mu.Lock()
	p.submissions = append(p.submissions, payload)
	p.mu.Unlock()
	if p.submitted != nil {
		p.submitted <- struct{}{}
	}
	if p.release != nil {
		select {
		case <-p.release:
		case <-ctx.Done():
			return pythonclient.JobStatus{}, ctx.Err()
		}
	}
	control := payload["training"].(Command)
	r := payload["range"].(backtest.Range)
	cursor := r.FromBarIndex
	if control.ExpectedCursor != nil {
		cursor = *control.ExpectedCursor
	}
	if control.Action == "advance" {
		cursor += *control.Count
	}
	if control.Action == "seek" {
		cursor = *control.TargetBarIndex
	}
	dataset := payload["dataset"].(map[string]any)
	snapshot := Snapshot{SessionID: payload["run_id"].(string), OperationID: control.OperationID, RunID: payload["run_id"].(string), RunSignature: payload["run_signature"].(string), DatasetID: dataset["dataset_id"].(string), DataRevision: dataset["data_revision"].(string), FromBarIndex: r.FromBarIndex, ToBarIndex: r.ToBarIndex, Cursor: cursor, ProcessedUntilBarIndex: cursor, State: "live", Reset: control.Action != "advance", Events: []map[string]any{}, Account: map[string]any{"event_type": "simulation_account", "known_at_bar_index": cursor}}
	if control.Action == "cancel" {
		snapshot.State = "cancelled"
	}
	ref := payload["output_path"].(string)
	path, _ := p.guard.Resolve(ref)
	_ = os.MkdirAll(path, 0750)
	data, _ := json.Marshal(snapshot)
	_ = os.WriteFile(filepath.Join(path, "snapshot.json"), data, 0600)
	sum := sha256.Sum256(data)
	marker, _ := json.Marshal(map[string]string{"snapshot_sha256": hex.EncodeToString(sum[:])})
	_ = os.WriteFile(filepath.Join(path, "_SUCCESS"), marker, 0600)
	return pythonclient.JobStatus{Status: "queued"}, nil
}
func (p *stubPython) Job(context.Context, string, string, string) (pythonclient.JobStatus, error) {
	if p.remoteError != nil {
		return pythonclient.JobStatus{Status: "failed", Error: p.remoteError}, nil
	}
	return pythonclient.JobStatus{Status: "completed", Progress: 1}, nil
}

func TestTrainingPreservesResourceProtectionError(t *testing.T) {
	s, python, _, input := fixture(t)
	python.remoteError = map[string]any{"code": "RESOURCE_MEMORY_LIMIT", "message": "测试内存保护"}
	created, err := s.Create(context.Background(), "r", "t", input)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		job, _, _ := s.Status(created.SessionID)
		if job.Status == jobs.Failed {
			if job.Error.Code != "RESOURCE_MEMORY_LIMIT" || job.Error.Message != "测试内存保护" {
				t.Fatalf("resource cause hidden: %+v", job.Error)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("training failure not reported")
}
func wait(t *testing.T, s *Service, id string) *Snapshot {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		job, snapshot, ok := s.Status(id)
		if ok && job.Status == jobs.Completed {
			return snapshot
		}
		if ok && job.Status == jobs.Failed {
			t.Fatalf("job failed: %+v", job.Error)
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("training job timeout")
	return nil
}
func fixture(t *testing.T) (*Service, *stubPython, *stubPrepare, CreateRequest) {
	t.Helper()
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	python := &stubPython{guard: guard}
	prepare := &stubPrepare{}
	s := NewService(guard, prepare, python, jobs.NewManager(), "1.0.0", time.Millisecond)
	input := CreateRequest{OperationID: "create", Backtest: backtest.Request{DatasetID: "TEST", DataRevision: "revision", Strategy: pythonclient.AlgorithmRef{Kind: "strategy", AlgorithmID: "divergence_segment_end_simulation"}, Parameters: map[string]any{}, Range: backtest.Range{FromBarIndex: 10, ToBarIndex: 100}, Execution: map[string]any{"fill_timing": "next_bar_open"}, Capital: map[string]any{"initial_cash_i64": 10000}}}
	return s, python, prepare, input
}
func TestTrainingTransportUsesPinnedFactsAndIdempotentCommands(t *testing.T) {
	s, python, prepare, input := fixture(t)
	ctx := context.Background()
	created, err := s.Create(ctx, "request", "trace", input)
	if err != nil {
		t.Fatal(err)
	}
	first := wait(t, s, created.SessionID)
	if first.Cursor != 10 {
		t.Fatal("creation advanced past start")
	}
	same, err := s.Create(ctx, "retry", "trace", input)
	if err != nil || same.Job.ID != created.Job.ID {
		t.Fatal("creation was not idempotent", err)
	}
	cursor, count := int64(10), int64(2)
	command := Command{SessionID: created.SessionID, OperationID: "step", Action: "advance", ExpectedCursor: &cursor, Count: &count}
	advanced, err := s.Command(ctx, "request", "trace", command)
	if err != nil {
		t.Fatal(err)
	}
	if wait(t, s, created.SessionID).Cursor != 12 {
		t.Fatal("wrong cursor")
	}
	repeated, err := s.Command(ctx, "request", "trace", command)
	if err != nil || repeated.Job.ID != advanced.Job.ID {
		t.Fatal("duplicate command submitted")
	}
	python.mu.Lock()
	defer python.mu.Unlock()
	if len(python.submissions) != 2 {
		t.Fatal("duplicate Python submission")
	}
	payload := python.submissions[1]
	if payload["algorithm"] != input.Backtest.Strategy || payload["calculation_mode"] != "causal_events" {
		t.Fatal("wrong pinned payload")
	}
	changed := command
	changed.OperationID = "stale"
	if _, err = s.Command(ctx, "request", "trace", changed); !errors.Is(err, ErrConflict) {
		t.Fatal("stale cursor accepted")
	}
	cursor = 12
	prepare.err = backtest.ErrRevisionMismatch
	changed.OperationID = "revision"
	if _, err = s.Command(ctx, "request", "trace", changed); !errors.Is(err, backtest.ErrRevisionMismatch) {
		t.Fatal("revision not checked")
	}
}
func TestTrainingRejectsConcurrentControlDuringPreparation(t *testing.T) {
	s, python, _, input := fixture(t)
	python.release = make(chan struct{})
	python.submitted = make(chan struct{}, 1)
	created, err := s.Create(context.Background(), "request", "trace", input)
	if err != nil {
		t.Fatal(err)
	}
	<-python.submitted
	cursor, count := int64(10), int64(1)
	_, err = s.Command(context.Background(), "r", "t", Command{SessionID: created.SessionID, OperationID: "premature", Action: "advance", ExpectedCursor: &cursor, Count: &count})
	if !errors.Is(err, ErrNotReady) {
		t.Fatal("accepted command during preparation")
	}
	close(python.release)
	wait(t, s, created.SessionID)
}
func TestTrainingSnapshotRejectsTamperingAndFutureFacts(t *testing.T) {
	s, _, _, input := fixture(t)
	created, err := s.Create(context.Background(), "r", "t", input)
	if err != nil {
		t.Fatal(err)
	}
	wait(t, s, created.SessionID)
	s.mu.Lock()
	current := s.sessions[created.SessionID]
	s.mu.Unlock()
	ref := "runs/training/" + current.id + "/commands/" + created.Job.ID
	path, _ := s.guard.Resolve(ref + "/snapshot.json")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var snapshot Snapshot
	_ = json.Unmarshal(data, &snapshot)
	snapshot.Account["known_at_bar_index"] = 11
	altered, _ := json.Marshal(snapshot)
	_ = os.WriteFile(path, altered, 0600)
	if _, err = s.readSnapshot(ref, current, "create"); err == nil {
		t.Fatal("tampered checksum accepted")
	}
	sum := sha256.Sum256(altered)
	marker, _ := json.Marshal(map[string]string{"snapshot_sha256": hex.EncodeToString(sum[:])})
	markerPath, _ := s.guard.Resolve(ref + "/_SUCCESS")
	_ = os.WriteFile(markerPath, marker, 0600)
	if _, err = s.readSnapshot(ref, current, "create"); err == nil {
		t.Fatal("future account accepted")
	}
}

func TestCompletedTrainingValidatesTheRunManifestStrategyFields(t *testing.T) {
	s, _, _, input := fixture(t)
	created, err := s.Create(context.Background(), "r", "t", input)
	if err != nil {
		t.Fatal(err)
	}
	first := wait(t, s, created.SessionID)
	s.mu.Lock()
	current := s.sessions[created.SessionID]
	s.mu.Unlock()
	ref := "runs/training/" + current.id + "/commands/" + created.Job.ID
	completed := *first
	completed.Cursor, completed.ProcessedUntilBarIndex = input.Backtest.Range.ToBarIndex, input.Backtest.Range.ToBarIndex
	completed.State, completed.CompletedRunRef = "completed", "runs/"+current.id
	completed.Account = map[string]any{"event_type": "simulation_account", "known_at_bar_index": completed.Cursor}
	data, _ := json.Marshal(completed)
	path, _ := s.guard.Resolve(ref + "/snapshot.json")
	_ = os.WriteFile(path, data, 0600)
	sum := sha256.Sum256(data)
	marker, _ := json.Marshal(map[string]string{"snapshot_sha256": hex.EncodeToString(sum[:])})
	path, _ = s.guard.Resolve(ref + "/_SUCCESS")
	_ = os.WriteFile(path, marker, 0600)
	directory, _ := s.guard.Resolve(completed.CompletedRunRef)
	_ = os.MkdirAll(directory, 0750)
	manifest := map[string]any{"run_id": current.id, "run_signature": current.signature, "dataset": map[string]any{"dataset_id": input.Backtest.DatasetID, "data_revision": input.Backtest.DataRevision}, "strategy": map[string]any{"strategy_id": input.Backtest.Strategy.AlgorithmID, "version": input.Backtest.Strategy.AlgorithmVersion, "source_hash": input.Backtest.Strategy.SourceHash}}
	manifestData, _ := json.Marshal(manifest)
	_ = os.WriteFile(filepath.Join(directory, "run.json"), manifestData, 0600)
	_ = os.WriteFile(filepath.Join(directory, "_SUCCESS"), []byte("{}"), 0600)
	if _, err := s.readSnapshot(ref, current, "create"); err != nil {
		t.Fatalf("valid formal manifest rejected: %v", err)
	}
	manifest["strategy"].(map[string]any)["version"] = "other"
	manifestData, _ = json.Marshal(manifest)
	_ = os.WriteFile(filepath.Join(directory, "run.json"), manifestData, 0600)
	if _, err := s.readSnapshot(ref, current, "create"); err == nil {
		t.Fatal("wrong formal strategy version accepted")
	}
}
