package maintenance

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var cacheKeyPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

type CleanupOptions struct {
	Kind      string
	OlderThan time.Duration
	DryRun    bool
	Now       time.Time
}

type Move struct {
	Source      string `json:"source"`
	Destination string `json:"destination"`
}

// CleanupCaches moves committed cache entries into data_root/trash. It never
// recursively deletes data and only accepts canonical SHA-256 cache names.
func CleanupCaches(guard *storage.PathGuard, options CleanupOptions) ([]Move, error) {
	kinds, err := selectedKinds(options.Kind)
	if err != nil {
		return nil, err
	}
	now := options.Now.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if options.OlderThan < 0 {
		return nil, errors.New("older-than must not be negative")
	}
	var moves []Move
	for _, kind := range kinds {
		root, err := guard.Resolve("cache/" + kind)
		if err != nil {
			return nil, err
		}
		entries, err := os.ReadDir(root)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read cache/%s: %w", kind, err)
		}
		for _, entry := range entries {
			if !cacheKeyPattern.MatchString(entry.Name()) {
				continue
			}
			source := filepath.Join(root, entry.Name())
			info, err := safeDirectory(source)
			if err != nil {
				return nil, err
			}
			success := filepath.Join(source, "_SUCCESS")
			successInfo, err := os.Lstat(success)
			if err != nil || !successInfo.Mode().IsRegular() {
				continue
			}
			ageFrom := info.ModTime()
			if successInfo.ModTime().After(ageFrom) {
				ageFrom = successInfo.ModTime()
			}
			if options.OlderThan > 0 && now.Sub(ageFrom) < options.OlderThan {
				continue
			}
			destinationRelative := filepath.ToSlash(filepath.Join("trash", "cache", now.Format("20060102T150405.000000000Z"), kind, entry.Name()))
			destination, err := guard.Resolve(destinationRelative)
			if err != nil {
				return nil, err
			}
			move := Move{Source: filepath.ToSlash(filepath.Join("cache", kind, entry.Name())), Destination: destinationRelative}
			moves = append(moves, move)
			if options.DryRun {
				continue
			}
			if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
				return nil, err
			}
			if err := os.Rename(source, destination); err != nil {
				return nil, fmt.Errorf("move %s to trash: %w", move.Source, err)
			}
		}
	}
	sort.Slice(moves, func(i, j int) bool { return moves[i].Source < moves[j].Source })
	return moves, nil
}

// RecoverStaleTemps moves stale, incomplete work directories to trash during
// startup. Only known immediate-child naming patterns are considered.
func RecoverStaleTemps(guard *storage.PathGuard, retention time.Duration, now time.Time) ([]Move, error) {
	if retention < 0 {
		return nil, errors.New("retention must not be negative")
	}
	if now.IsZero() {
		now = time.Now().UTC()
	}
	type rootPattern struct {
		relative string
		accept   func(string) bool
	}
	roots := []rootPattern{
		{"tmp", func(name string) bool { return strings.HasPrefix(name, "import-") }},
		{"cache/indicators", temporaryCacheName},
		{"cache/chan", temporaryCacheName},
		{"cache/replay", temporaryCacheName},
		{"runs", temporaryCacheName},
		{"studies", temporaryCacheName},
		{"comparisons", temporaryCacheName},
	}
	var moves []Move
	stamp := now.UTC().Format("20060102T150405.000000000Z")
	for _, item := range roots {
		root, err := guard.Resolve(item.relative)
		if err != nil {
			return nil, err
		}
		entries, err := os.ReadDir(root)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		for _, entry := range entries {
			if !item.accept(entry.Name()) {
				continue
			}
			// Atomic JSON writers use the same .name.tmp-* convention as
			// incomplete result directories. Those regular files may briefly
			// coexist with startup recovery and are owned by the writer, so only
			// directory-shaped work products belong to this cleanup routine.
			if !entry.IsDir() {
				if entry.Type()&os.ModeSymlink != 0 {
					return nil, fmt.Errorf("refusing link: %s", filepath.Join(root, entry.Name()))
				}
				continue
			}
			source := filepath.Join(root, entry.Name())
			info, err := safeDirectory(source)
			if err != nil {
				return nil, err
			}
			if now.Sub(info.ModTime()) < retention {
				continue
			}
			destinationRelative := filepath.ToSlash(filepath.Join("trash", "interrupted", stamp, strings.ReplaceAll(item.relative, "/", "_"), entry.Name()))
			destination, err := guard.Resolve(destinationRelative)
			if err != nil {
				return nil, err
			}
			if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
				return nil, err
			}
			if err := os.Rename(source, destination); err != nil {
				return nil, fmt.Errorf("recover stale temporary directory: %w", err)
			}
			moves = append(moves, Move{Source: filepath.ToSlash(filepath.Join(item.relative, entry.Name())), Destination: destinationRelative})
		}
	}
	return moves, nil
}

func selectedKinds(kind string) ([]string, error) {
	if kind == "" || kind == "all" {
		return []string{"indicators", "chan", "replay"}, nil
	}
	for _, allowed := range []string{"indicators", "chan", "replay"} {
		if kind == allowed {
			return []string{kind}, nil
		}
	}
	return nil, fmt.Errorf("unsupported cache kind %q", kind)
}

func temporaryCacheName(name string) bool {
	return strings.HasPrefix(name, ".") && strings.Contains(name, ".tmp-")
}

func safeDirectory(path string) (os.FileInfo, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return nil, fmt.Errorf("refusing non-directory or link: %s", path)
	}
	return info, nil
}
