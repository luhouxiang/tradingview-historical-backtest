package jobs

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func TestNewIDIsValidAcrossProcessContract(t *testing.T) {
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`).MatchString(NewID()) {
		t.Fatalf("job ID is not accepted by the internal contract: %q", NewID())
	}
}

func TestPersistentManagerMarksActiveJobsInterruptedOnRestart(t *testing.T) {
	guard, err := storage.NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	directory, _ := guard.Resolve("tasks/jobs")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	before := Job{ID: "job-restart", Kind: "backtest", Status: Running, Progress: 0.4, CreatedAt: time.Now().Add(-time.Minute).UTC(), UpdatedAt: time.Now().Add(-time.Second).UTC()}
	data, _ := json.Marshal(before)
	if err := os.WriteFile(filepath.Join(directory, before.ID+".json"), data, 0o640); err != nil {
		t.Fatal(err)
	}
	manager, err := NewPersistentManager(guard)
	if err != nil {
		t.Fatal(err)
	}
	after, ok := manager.Get(before.ID)
	if !ok || after.Status != Interrupted || after.Error == nil || after.Error.Code != "PROCESS_RESTARTED" {
		t.Fatalf("unexpected restored job: %#v", after)
	}
	persisted, err := os.ReadFile(filepath.Join(directory, before.ID+".json"))
	if err != nil || !regexp.MustCompile(`"status":\s*"interrupted"`).Match(persisted) {
		t.Fatalf("interrupted status was not persisted: %v %s", err, persisted)
	}
}

func TestPersistentManagerAcceptsUTF8BOM(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	directory, _ := guard.Resolve("tasks/jobs")
	_ = os.MkdirAll(directory, 0o750)
	job := Job{ID: "job-bom", Kind: "test", Status: Failed, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	data, _ := json.Marshal(job)
	data = append([]byte{0xEF, 0xBB, 0xBF}, data...)
	if err := os.WriteFile(filepath.Join(directory, "job-bom.json"), data, 0o640); err != nil {
		t.Fatal(err)
	}
	manager, err := NewPersistentManager(guard)
	if err != nil {
		t.Fatal(err)
	}
	if restored, ok := manager.Get("job-bom"); !ok || restored.Status != Failed {
		t.Fatalf("unexpected BOM restore: %#v", restored)
	}
}

func TestPersistentManagerPersistsCompletion(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	manager, err := NewPersistentManager(guard)
	if err != nil {
		t.Fatal(err)
	}
	manager.RecordCompleted("run-1", "backtest", "runs/run-1")
	if _, ok := manager.SetMetadata("run-1", map[string]string{"run_signature": "sha256:abc"}); !ok {
		t.Fatal("metadata target was not found")
	}
	restored, err := NewPersistentManager(guard)
	if err != nil {
		t.Fatal(err)
	}
	job, ok := restored.Get("run-1")
	if !ok || job.Status != Completed || job.ResultRef != "runs/run-1" || job.Metadata["run_signature"] != "sha256:abc" {
		t.Fatalf("unexpected restored completion: %#v", job)
	}
}

func TestPersistentManagerDoesNotRewriteTerminalHistory(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	directory, _ := guard.Resolve("tasks/jobs")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	oldTime := time.Date(2025, 1, 2, 3, 4, 5, 0, time.UTC)
	for index, status := range []Status{Completed, Failed, Cancelled, Interrupted} {
		job := Job{ID: fmt.Sprintf("job-terminal-%d", index), Kind: "test", Status: status, CreatedAt: oldTime, UpdatedAt: oldTime}
		data, _ := json.Marshal(job)
		path := filepath.Join(directory, job.ID+".json")
		if err := os.WriteFile(path, data, 0o640); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, oldTime, oldTime); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := NewPersistentManager(guard); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(directory)
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			t.Fatal(err)
		}
		if !info.ModTime().Equal(oldTime) {
			t.Fatalf("terminal job %s was rewritten during restore: %s", entry.Name(), info.ModTime())
		}
	}
}

func TestManagerCompletesJob(t *testing.T) {
	manager := NewManager()
	job := manager.Submit("test", func(_ context.Context, progress func(float64)) (string, error) {
		progress(0.5)
		return "result.json", nil
	})
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		current, ok := manager.Get(job.ID)
		if ok && current.Status == Completed {
			if current.ResultRef != "result.json" || current.Progress != 1 {
				t.Fatalf("unexpected job: %#v", current)
			}
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("job did not complete")
}

func TestManagerCancelsJob(t *testing.T) {
	manager := NewManager()
	job := manager.Submit("test", func(ctx context.Context, _ func(float64)) (string, error) {
		<-ctx.Done()
		return "", ctx.Err()
	})
	if _, ok := manager.Cancel(job.ID); !ok {
		t.Fatal("cancel did not find job")
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		current, _ := manager.Get(job.ID)
		if current.Status == Cancelled {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("job did not cancel")
}
