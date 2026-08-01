package jobs

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type Status string

const (
	Queued      Status = "queued"
	Running     Status = "running"
	Cancelling  Status = "cancelling"
	Completed   Status = "completed"
	Failed      Status = "failed"
	Cancelled   Status = "cancelled"
	Interrupted Status = "interrupted"
)

type Error struct {
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Details map[string]any `json:"details,omitempty"`
}

type Job struct {
	ID        string            `json:"job_id"`
	Kind      string            `json:"kind"`
	Status    Status            `json:"status"`
	Progress  float64           `json:"progress"`
	ResultRef string            `json:"result_ref,omitempty"`
	Metadata  map[string]string `json:"metadata,omitempty"`
	Error     *Error            `json:"error,omitempty"`
	CreatedAt time.Time         `json:"created_at"`
	UpdatedAt time.Time         `json:"updated_at"`
	cancel    context.CancelFunc
}

type Work func(context.Context, func(float64)) (string, error)

type WorkError struct {
	Code    string
	Message string
	Cause   error
}

func (e *WorkError) Error() string { return e.Message }
func (e *WorkError) Unwrap() error { return e.Cause }

func Fail(code, message string, cause error) error {
	return &WorkError{Code: code, Message: message, Cause: cause}
}

type Manager struct {
	mu        sync.RWMutex
	persistMu sync.Mutex
	jobs      map[string]*Job
	storeDir  string
}

func NewManager() *Manager { return &Manager{jobs: make(map[string]*Job)} }

// NewPersistentManager restores durable job summaries. Work cannot survive a
// process restart, so every formerly active job is atomically marked
// interrupted before the manager is returned.
func NewPersistentManager(guard *storage.PathGuard) (*Manager, error) {
	directory, err := guard.Resolve("tasks/jobs")
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return nil, fmt.Errorf("create job store: %w", err)
	}
	manager := &Manager{jobs: make(map[string]*Job), storeDir: directory}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, fmt.Errorf("read job store: %w", err)
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(directory, entry.Name()))
		if err != nil {
			return nil, fmt.Errorf("read job %s: %w", entry.Name(), err)
		}
		data = bytes.TrimPrefix(data, []byte{0xEF, 0xBB, 0xBF})
		var job Job
		if err := json.Unmarshal(data, &job); err != nil || !validID(job.ID) {
			return nil, fmt.Errorf("invalid persisted job %s", entry.Name())
		}
		if job.Status == Queued || job.Status == Running || job.Status == Cancelling {
			job.Status = Interrupted
			job.UpdatedAt = time.Now().UTC()
			job.Error = &Error{Code: "PROCESS_RESTARTED", Message: "Job was interrupted by a process restart"}
		}
		manager.jobs[job.ID] = &job
		if err := manager.persist(&job); err != nil {
			return nil, err
		}
	}
	return manager, nil
}

func (m *Manager) Submit(kind string, work Work) *Job {
	return m.SubmitID(NewID(), kind, work)
}

func (m *Manager) SubmitID(id, kind string, work Work) *Job {
	ctx, cancel := context.WithCancel(context.Background())
	now := time.Now().UTC()
	job := &Job{ID: id, Kind: kind, Status: Queued, CreatedAt: now, UpdatedAt: now, cancel: cancel}
	m.mu.Lock()
	m.jobs[job.ID] = job
	_ = m.persist(job)
	m.mu.Unlock()
	result := clone(job)
	go m.run(ctx, job.ID, work)
	return result
}

func (m *Manager) RecordCompleted(id, kind, resultRef string) *Job {
	now := time.Now().UTC()
	job := &Job{ID: id, Kind: kind, Status: Completed, Progress: 1, ResultRef: resultRef, CreatedAt: now, UpdatedAt: now}
	m.mu.Lock()
	m.jobs[id] = job
	_ = m.persist(job)
	m.mu.Unlock()
	return clone(job)
}

func (m *Manager) run(ctx context.Context, id string, work Work) {
	m.update(id, func(job *Job) {
		job.Status = Running
		job.UpdatedAt = time.Now().UTC()
	})
	result, err := work(ctx, func(progress float64) {
		if progress < 0 {
			progress = 0
		}
		if progress > 1 {
			progress = 1
		}
		m.update(id, func(job *Job) { job.Progress, job.UpdatedAt = progress, time.Now().UTC() })
	})
	m.update(id, func(job *Job) {
		job.UpdatedAt = time.Now().UTC()
		job.cancel = nil
		switch {
		case errors.Is(err, context.Canceled) || ctx.Err() != nil:
			job.Status = Cancelled
		case err != nil:
			job.Status = Failed
			var workError *WorkError
			if errors.As(err, &workError) {
				job.Error = &Error{Code: workError.Code, Message: workError.Message}
			} else {
				job.Error = &Error{Code: "JOB_FAILED", Message: "Job failed"}
			}
		default:
			job.Status = Completed
			job.Progress = 1
			job.ResultRef = result
		}
	})
}

func (m *Manager) Get(id string) (*Job, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	job, ok := m.jobs[id]
	return clone(job), ok
}

func (m *Manager) SetMetadata(id string, metadata map[string]string) (*Job, bool) {
	m.mu.Lock()
	job, ok := m.jobs[id]
	if !ok {
		m.mu.Unlock()
		return nil, false
	}
	job.Metadata = make(map[string]string, len(metadata))
	for key, value := range metadata {
		job.Metadata[key] = value
	}
	job.UpdatedAt = time.Now().UTC()
	copy := clone(job)
	_ = m.persist(copy)
	m.mu.Unlock()
	return copy, true
}

func (m *Manager) Cancel(id string) (*Job, bool) {
	m.mu.Lock()
	job, ok := m.jobs[id]
	if !ok {
		m.mu.Unlock()
		return nil, false
	}
	if job.Status == Queued || job.Status == Running {
		job.Status = Cancelling
		job.UpdatedAt = time.Now().UTC()
		if job.cancel != nil {
			job.cancel()
		}
	}
	copy := clone(job)
	_ = m.persist(copy)
	m.mu.Unlock()
	return copy, true
}

func (m *Manager) update(id string, fn func(*Job)) {
	m.mu.Lock()
	var copy *Job
	if job := m.jobs[id]; job != nil {
		fn(job)
		copy = clone(job)
	}
	_ = m.persist(copy)
	m.mu.Unlock()
}

func (m *Manager) persist(job *Job) error {
	if m.storeDir == "" || job == nil {
		return nil
	}
	if !validID(job.ID) {
		return fmt.Errorf("invalid job id %q", job.ID)
	}
	m.persistMu.Lock()
	defer m.persistMu.Unlock()
	data, err := json.MarshalIndent(job, "", "  ")
	if err != nil {
		return fmt.Errorf("encode job %s: %w", job.ID, err)
	}
	if err := storage.AtomicWriteFile(filepath.Join(m.storeDir, job.ID+".json"), append(data, '\n'), 0o640); err != nil {
		return fmt.Errorf("persist job %s: %w", job.ID, err)
	}
	return nil
}

var jobIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func validID(id string) bool { return jobIDPattern.MatchString(id) }

func clone(job *Job) *Job {
	if job == nil {
		return nil
	}
	copy := *job
	if job.Metadata != nil {
		copy.Metadata = make(map[string]string, len(job.Metadata))
		for key, value := range job.Metadata {
			copy.Metadata[key] = value
		}
	}
	copy.cancel = nil
	return &copy
}

func NewID() string {
	bytes := make([]byte, 10)
	_, _ = rand.Read(bytes)
	return "job-" + time.Now().UTC().Format("20060102T150405000000000") + "-" + hex.EncodeToString(bytes)
}
