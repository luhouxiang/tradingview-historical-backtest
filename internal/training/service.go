package training

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrInvalid  = errors.New("invalid training command")
	ErrConflict = errors.New("training command conflicts with session")
	ErrNotReady = errors.New("training session is preparing or advancing")
)
var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type Preparer interface {
	Prepare(context.Context, string, string, backtest.Request) (backtest.Request, catalog.DatasetMeta, error)
}
type Command struct {
	SessionID      string `json:"session_id"`
	OperationID    string `json:"operation_id"`
	Action         string `json:"action"`
	ExpectedCursor *int64 `json:"expected_cursor,omitempty"`
	Count          *int64 `json:"count,omitempty"`
	TargetBarIndex *int64 `json:"target_bar_index,omitempty"`
}
type CreateRequest struct {
	OperationID string           `json:"operation_id"`
	Backtest    backtest.Request `json:"backtest"`
}
type Snapshot struct {
	SessionID              string           `json:"session_id"`
	OperationID            string           `json:"operation_id"`
	DatasetID              string           `json:"dataset_id"`
	DataRevision           string           `json:"data_revision"`
	RunID                  string           `json:"run_id"`
	RunSignature           string           `json:"run_signature"`
	FromBarIndex           int64            `json:"from_bar_index"`
	ToBarIndex             int64            `json:"to_bar_index"`
	Cursor                 int64            `json:"cursor"`
	ProcessedUntilBarIndex int64            `json:"processed_until_bar_index"`
	State                  string           `json:"state"`
	Reset                  bool             `json:"reset"`
	Events                 []map[string]any `json:"events"`
	Account                map[string]any   `json:"account"`
	CompletedRunRef        string           `json:"completed_run_ref,omitempty"`
}
type Submission struct {
	SessionID string
	Job       *jobs.Job
}
type operation struct {
	fingerprint string
	jobID       string
}
type session struct {
	id, signature string
	request       backtest.Request
	meta          catalog.DatasetMeta
	latestJob     string
	snapshot      *Snapshot
	operations    map[string]operation
}
type Service struct {
	guard    *storage.PathGuard
	prepare  Preparer
	python   backtest.Python
	manager  *jobs.Manager
	contract string
	poll     time.Duration
	mu       sync.Mutex
	sessions map[string]*session
	creates  map[string]operation
}

func NewService(guard *storage.PathGuard, prepare Preparer, python backtest.Python, manager *jobs.Manager, contract string, poll time.Duration) *Service {
	return &Service{guard: guard, prepare: prepare, python: python, manager: manager, contract: contract, poll: poll, sessions: map[string]*session{}, creates: map[string]operation{}}
}
func fingerprint(value any) string {
	data, _ := json.Marshal(value)
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
func (s *Service) Create(ctx context.Context, requestID, traceID string, input CreateRequest) (Submission, error) {
	if !idPattern.MatchString(input.OperationID) || input.Backtest.Strategy.AlgorithmID != "divergence_segment_end_simulation" || input.Backtest.RankingContext != nil {
		return Submission{}, ErrInvalid
	}
	request, meta, err := s.prepare.Prepare(ctx, requestID, traceID, input.Backtest)
	if err != nil {
		return Submission{}, err
	}
	if request.Execution["fill_timing"] != "next_bar_open" {
		return Submission{}, ErrInvalid
	}
	signature, err := backtest.Signature(request, s.python.Health(ctx).Version())
	if err != nil {
		return Submission{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if previous, ok := s.creates[input.OperationID]; ok {
		if previous.fingerprint != signature {
			return Submission{}, ErrConflict
		}
		existing := s.sessions[previous.jobID]
		// Creation retry returns its original job, not a later advance job.
		original := existing.operations[input.OperationID]
		job, _ := s.manager.Get(original.jobID)
		return Submission{existing.id, job}, nil
	}
	id := "training-" + jobs.NewID()
	current := &session{id: id, signature: signature, request: request, meta: meta, operations: map[string]operation{}}
	s.sessions[id] = current
	s.creates[input.OperationID] = operation{signature, id}
	return s.submitLocked(current, requestID, traceID, Command{SessionID: id, OperationID: input.OperationID, Action: "create"}), nil
}
func (s *Service) Command(ctx context.Context, requestID, traceID string, command Command) (Submission, error) {
	if !idPattern.MatchString(command.SessionID) || !idPattern.MatchString(command.OperationID) || command.ExpectedCursor == nil || *command.ExpectedCursor < 0 {
		return Submission{}, ErrInvalid
	}
	switch command.Action {
	case "advance":
		if command.Count == nil || *command.Count < 1 || *command.Count > 100 {
			return Submission{}, ErrInvalid
		}
	case "seek":
		if command.TargetBarIndex == nil || *command.TargetBarIndex < 0 {
			return Submission{}, ErrInvalid
		}
	case "cancel":
	default:
		return Submission{}, ErrInvalid
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current, ok := s.sessions[command.SessionID]
	if !ok {
		return Submission{}, catalog.ErrNotFound
	}
	if previous, ok := current.operations[command.OperationID]; ok {
		if previous.fingerprint != fingerprint(command) {
			return Submission{}, ErrConflict
		}
		job, _ := s.manager.Get(previous.jobID)
		return Submission{current.id, job}, nil
	}
	job, _ := s.manager.Get(current.latestJob)
	if job.Status == jobs.Queued || job.Status == jobs.Running || job.Status == jobs.Cancelling {
		return Submission{}, ErrNotReady
	}
	if current.snapshot == nil {
		return Submission{}, ErrNotReady
	}
	if *command.ExpectedCursor != current.snapshot.Cursor {
		return Submission{}, ErrConflict
	}
	if command.Action == "seek" && (*command.TargetBarIndex < current.request.Range.FromBarIndex || *command.TargetBarIndex > current.snapshot.ProcessedUntilBarIndex) {
		return Submission{}, ErrInvalid
	}
	if command.Action == "advance" {
		if current.snapshot.State == "cancelled" || current.snapshot.State == "interrupted" {
			return Submission{}, ErrConflict
		}
		normalized, _, err := s.prepare.Prepare(ctx, requestID, traceID, current.request)
		if err != nil {
			return Submission{}, err
		}
		signature, err := backtest.Signature(normalized, s.python.Health(ctx).Version())
		if err != nil {
			return Submission{}, err
		}
		if signature != current.signature {
			return Submission{}, ErrConflict
		}
	}
	return s.submitLocked(current, requestID, traceID, command), nil
}
func (s *Service) Status(id string) (*jobs.Job, *Snapshot, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	current, ok := s.sessions[id]
	if !ok {
		return nil, nil, false
	}
	// A cancelled transport job may have committed its already executed prefix.
	// Refresh only a known command's guarded immutable reference, never an
	// arbitrary latest.json path or an older operation replay.
	if path, err := s.guard.Resolve("runs/training/" + id + "/latest.json"); err == nil {
		if data, err := os.ReadFile(path); err == nil {
			var pointer struct {
				Ref string `json:"snapshot_ref"`
			}
			if json.Unmarshal(data, &pointer) == nil {
				for operationID, operation := range current.operations {
					if pointer.Ref == "runs/training/"+id+"/commands/"+operation.jobID && operation.jobID == current.latestJob {
						if snapshot, err := s.readSnapshot(pointer.Ref, current, operationID); err == nil {
							current.snapshot = snapshot
						}
					}
				}
			}
		}
	}
	job, ok := s.manager.Get(current.latestJob)
	return job, current.snapshot, ok
}
func (s *Service) submitLocked(current *session, requestID, traceID string, command Command) Submission {
	jobID := jobs.NewID()
	ref := "runs/training/" + current.id + "/commands/" + jobID
	current.latestJob = jobID
	current.operations[command.OperationID] = operation{fingerprint(command), jobID}
	request := current.request
	barsPath, metaPath := "", ""
	for _, file := range current.meta.Files {
		if file.Role == "bars" {
			barsPath = file.Path
			metaPath = strings.TrimSuffix(file.Path, "bars.parquet") + "meta.json"
			break
		}
	}
	payload := map[string]any{"contract_version": s.contract, "request_id": requestID, "trace_id": traceID, "job_id": jobID, "run_id": current.id, "run_signature": current.signature, "dataset": map[string]any{"dataset_id": request.DatasetID, "data_revision": request.DataRevision, "bars_path": barsPath, "meta_path": metaPath}, "algorithm": request.Strategy, "parameters": request.Parameters, "range": request.Range, "execution": request.Execution, "capital": request.Capital, "random_seed": request.RandomSeed, "output_path": ref, "training": command}
	payload["calculation_mode"] = "causal_events"
	payload["cache_key"] = current.signature
	if request.RiskOverlay != nil {
		payload["risk_overlay"] = request.RiskOverlay
	}
	job := s.manager.SubmitID(jobID, "training", func(ctx context.Context, progress func(float64)) (string, error) {
		if _, err := s.python.Submit(ctx, "training", requestID, traceID, payload); err != nil {
			return "", jobs.Fail("PYTHON_SUBMIT_FAILED", "训练命令提交失败", err)
		}
		ticker := time.NewTicker(s.poll)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				cancelCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				_ = s.python.Cancel(cancelCtx, jobID, requestID, traceID)
				cancel()
				return "", ctx.Err()
			case <-ticker.C:
				status, err := s.python.Job(ctx, jobID, requestID, traceID)
				if err != nil {
					return "", jobs.Fail("PYTHON_POLL_FAILED", "训练命令状态读取失败", err)
				}
				progress(status.Progress)
				switch status.Status {
				case "completed":
					snapshot, err := s.readSnapshot(ref, current, command.OperationID)
					if err != nil {
						return "", jobs.Fail("TRAINING_COMMIT_INVALID", "训练记录未成功提交", err)
					}
					s.mu.Lock()
					current.snapshot = snapshot
					s.mu.Unlock()
					return ref, nil
				case "failed":
					if status.Error["code"] == "RESOURCE_MEMORY_LIMIT" {
						message, _ := status.Error["message"].(string)
						if strings.TrimSpace(message) == "" {
							message = "训练已达到内存保护阈值；已提交的成交记录保留，请重新开始训练"
						}
						return "", jobs.Fail("RESOURCE_MEMORY_LIMIT", message, nil)
					}
					return "", jobs.Fail("PYTHON_TRAINING_FAILED", "训练执行失败，请查看日志或重新开始训练", nil)
				case "cancelled", "interrupted":
					return "", context.Canceled
				}
			}
		}
	})
	return Submission{current.id, job}
}
func (s *Service) readSnapshot(ref string, current *session, operationID string) (*Snapshot, error) {
	path, err := s.guard.Resolve(ref + "/snapshot.json")
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	markerPath, err := s.guard.Resolve(ref + "/_SUCCESS")
	if err != nil {
		return nil, err
	}
	marker, err := os.ReadFile(markerPath)
	if err != nil {
		return nil, err
	}
	var checksum struct {
		SHA string `json:"snapshot_sha256"`
	}
	if json.Unmarshal(marker, &checksum) != nil {
		return nil, ErrInvalid
	}
	sum := sha256.Sum256(data)
	if checksum.SHA != hex.EncodeToString(sum[:]) {
		return nil, ErrInvalid
	}
	var snapshot Snapshot
	if err = json.Unmarshal(data, &snapshot); err != nil {
		return nil, err
	}
	r := current.request
	if snapshot.SessionID != current.id || snapshot.RunID != current.id || snapshot.OperationID != operationID || snapshot.RunSignature != current.signature || snapshot.DatasetID != r.DatasetID || snapshot.DataRevision != r.DataRevision || snapshot.FromBarIndex != r.Range.FromBarIndex || snapshot.ToBarIndex != r.Range.ToBarIndex || snapshot.Cursor < r.Range.FromBarIndex || snapshot.Cursor > snapshot.ProcessedUntilBarIndex || snapshot.ProcessedUntilBarIndex > r.Range.ToBarIndex {
		return nil, ErrInvalid
	}
	switch snapshot.State {
	case "live", "lookback", "completed", "cancelled", "interrupted":
	default:
		return nil, ErrInvalid
	}
	lastSeq := float64(-1)
	for _, event := range snapshot.Events {
		known, ok := event["known_at_bar_index"].(float64)
		seq, seqOK := event["event_seq"].(float64)
		if !ok || !seqOK || known < 0 || known > float64(snapshot.Cursor) || seq <= lastSeq {
			return nil, ErrInvalid
		}
		lastSeq = seq
	}
	if snapshot.Account["event_type"] != "simulation_account" || snapshot.Account["known_at_bar_index"] != float64(snapshot.Cursor) {
		return nil, ErrInvalid
	}
	if snapshot.State == "completed" || snapshot.CompletedRunRef != "" {
		if snapshot.ProcessedUntilBarIndex != r.Range.ToBarIndex || (snapshot.State == "completed" && snapshot.Cursor != r.Range.ToBarIndex) {
			return nil, ErrInvalid
		}
		if snapshot.CompletedRunRef != "runs/"+current.id {
			return nil, ErrInvalid
		}
		success, err := s.guard.Resolve(snapshot.CompletedRunRef + "/_SUCCESS")
		if err != nil {
			return nil, err
		}
		if _, err = os.Stat(success); err != nil {
			return nil, err
		}
		manifestPath, err := s.guard.Resolve(snapshot.CompletedRunRef + "/run.json")
		if err != nil {
			return nil, err
		}
		manifestData, err := os.ReadFile(manifestPath)
		if err != nil {
			return nil, err
		}
		var manifest map[string]any
		if json.Unmarshal(manifestData, &manifest) != nil || manifest["run_id"] != current.id || manifest["run_signature"] != current.signature {
			return nil, ErrInvalid
		}
		dataset, ok := manifest["dataset"].(map[string]any)
		if !ok || dataset["dataset_id"] != r.DatasetID || dataset["data_revision"] != r.DataRevision {
			return nil, ErrInvalid
		}
		strategy, ok := manifest["strategy"].(map[string]any)
		if !ok || strategy["strategy_id"] != r.Strategy.AlgorithmID || strategy["version"] != r.Strategy.AlgorithmVersion || strategy["source_hash"] != r.Strategy.SourceHash {
			return nil, ErrInvalid
		}
	}
	return &snapshot, nil
}
