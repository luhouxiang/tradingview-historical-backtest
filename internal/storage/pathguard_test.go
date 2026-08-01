package storage

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPathGuardAcceptsRelativePath(t *testing.T) {
	g, err := NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	got, err := g.Resolve("normalized/SHFE.AO2609.5m/meta.json")
	if err != nil {
		t.Fatal(err)
	}
	if !within(g.Root(), got) {
		t.Fatalf("resolved outside root: %s", got)
	}
}

func TestPathGuardRejectsTraversalAndAbsolute(t *testing.T) {
	g, err := NewPathGuard(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, input := range []string{"../secret", "a/../../secret", filepath.Join(filepath.VolumeName(g.Root())+string(filepath.Separator), "secret")} {
		if _, err := g.Resolve(input); !errors.Is(err, ErrPathEscape) {
			t.Errorf("Resolve(%q) error = %v", input, err)
		}
	}
}

func TestPathGuardRejectsSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	link := filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		if runtime.GOOS == "windows" {
			t.Skipf("symlink privilege unavailable: %v", err)
		}
		t.Fatal(err)
	}
	g, err := NewPathGuard(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := g.Resolve("escape/file.json"); !errors.Is(err, ErrPathEscape) {
		t.Fatalf("expected escape rejection, got %v", err)
	}
}
