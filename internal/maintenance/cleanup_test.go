package maintenance

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func TestCleanupCachesMovesOnlyCommittedCanonicalEntries(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	key := strings.Repeat("a", 64)
	committed, _ := guard.Resolve("cache/chan/" + key)
	incomplete, _ := guard.Resolve("cache/chan/" + strings.Repeat("b", 64))
	noncanonical, _ := guard.Resolve("cache/chan/example")
	for _, path := range []string{committed, incomplete, noncanonical} {
		if err := os.MkdirAll(path, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(committed, "_SUCCESS"), nil, 0o640); err != nil {
		t.Fatal(err)
	}
	moves, err := CleanupCaches(guard, CleanupOptions{Kind: "chan", Now: time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)})
	if err != nil || len(moves) != 1 {
		t.Fatalf("moves=%#v err=%v", moves, err)
	}
	if _, err := os.Stat(committed); !os.IsNotExist(err) {
		t.Fatal("committed cache was not moved")
	}
	for _, path := range []string{incomplete, noncanonical} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("unexpected move of %s: %v", path, err)
		}
	}
}

func TestCleanupCachesDryRunAndAgeFilter(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	directory, _ := guard.Resolve("cache/replay/" + strings.Repeat("c", 64))
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	success := filepath.Join(directory, "_SUCCESS")
	if err := os.WriteFile(success, nil, 0o640); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	moves, err := CleanupCaches(guard, CleanupOptions{Kind: "replay", OlderThan: time.Hour, DryRun: true, Now: now})
	if err != nil || len(moves) != 0 {
		t.Fatalf("new cache selected: %#v %v", moves, err)
	}
	old := now.Add(-2 * time.Hour)
	_ = os.Chtimes(directory, old, old)
	_ = os.Chtimes(success, old, old)
	moves, err = CleanupCaches(guard, CleanupOptions{Kind: "replay", OlderThan: time.Hour, DryRun: true, Now: now})
	if err != nil || len(moves) != 1 {
		t.Fatalf("old cache not selected: %#v %v", moves, err)
	}
	if _, err := os.Stat(directory); err != nil {
		t.Fatal("dry run changed the cache")
	}
}

func TestRecoverStaleTempsMovesKnownNames(t *testing.T) {
	guard, _ := storage.NewPathGuard(t.TempDir())
	temporary, _ := guard.Resolve("cache/indicators/.abc.tmp-deadbeef")
	studyTemporary, _ := guard.Resolve("studies/.study-1.tmp-deadbeef")
	keep, _ := guard.Resolve("cache/indicators/not-a-temp")
	for _, path := range []string{temporary, studyTemporary, keep} {
		if err := os.MkdirAll(path, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now().UTC()
	old := now.Add(-25 * time.Hour)
	_ = os.Chtimes(temporary, old, old)
	_ = os.Chtimes(studyTemporary, old, old)
	moves, err := RecoverStaleTemps(guard, 24*time.Hour, now)
	if err != nil || len(moves) != 2 {
		t.Fatalf("moves=%#v err=%v", moves, err)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatal("unrecognized directory was moved")
	}
}

func TestCleanupCachesRejectsSymlinkEntry(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation usually requires elevated Windows privileges")
	}
	guard, _ := storage.NewPathGuard(t.TempDir())
	outside := t.TempDir()
	root, _ := guard.Resolve("cache/chan")
	if err := os.MkdirAll(root, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, strings.Repeat("d", 64))); err != nil {
		t.Fatal(err)
	}
	if _, err := CleanupCaches(guard, CleanupOptions{Kind: "chan"}); err == nil {
		t.Fatal("expected symlink rejection")
	}
}
