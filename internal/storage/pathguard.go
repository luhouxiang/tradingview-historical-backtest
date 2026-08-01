package storage

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

var ErrPathEscape = errors.New("path escapes data_root")

type PathGuard struct {
	root string
}

func NewPathGuard(root string) (*PathGuard, error) {
	if root == "" {
		return nil, errors.New("data_root is required")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve data_root: %w", err)
	}
	if err := os.MkdirAll(abs, 0o750); err != nil {
		return nil, fmt.Errorf("create data_root: %w", err)
	}
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return nil, fmt.Errorf("evaluate data_root: %w", err)
	}
	return &PathGuard{root: filepath.Clean(real)}, nil
}

func (g *PathGuard) Root() string { return g.root }

func (g *PathGuard) Resolve(relative string) (string, error) {
	if relative == "" || filepath.IsAbs(relative) || filepath.VolumeName(relative) != "" {
		return "", ErrPathEscape
	}
	normalized := strings.ReplaceAll(relative, "\\", "/")
	for _, part := range strings.Split(normalized, "/") {
		if part == ".." {
			return "", ErrPathEscape
		}
	}
	target := filepath.Join(g.root, filepath.FromSlash(normalized))
	if !within(g.root, target) {
		return "", ErrPathEscape
	}
	current := g.root
	parts := strings.Split(filepath.Clean(filepath.FromSlash(normalized)), string(filepath.Separator))
	for _, part := range parts {
		if part == "." || part == "" {
			continue
		}
		current = filepath.Join(current, part)
		_, err := os.Lstat(current)
		if err != nil {
			if os.IsNotExist(err) {
				break
			}
			return "", fmt.Errorf("inspect guarded path: %w", err)
		}
		real, err := filepath.EvalSymlinks(current)
		if err != nil {
			return "", fmt.Errorf("evaluate guarded path: %w", err)
		}
		if !within(g.root, real) {
			return "", ErrPathEscape
		}
	}
	return filepath.Clean(target), nil
}

func (g *PathGuard) Relative(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil || !within(g.root, abs) {
		return "", ErrPathEscape
	}
	rel, err := filepath.Rel(g.root, abs)
	if err != nil || rel == "." {
		return "", ErrPathEscape
	}
	return filepath.ToSlash(rel), nil
}

func within(root, target string) bool {
	rel, err := filepath.Rel(root, target)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
