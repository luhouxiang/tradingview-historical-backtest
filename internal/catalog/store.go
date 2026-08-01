package catalog

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var ErrNotFound = errors.New("dataset not found")

type Store struct {
	mu       sync.RWMutex
	guard    *storage.PathGuard
	path     string
	document Document
}

func NewStore(guard *storage.PathGuard) (*Store, error) {
	path, err := guard.Resolve("catalog/catalog.json")
	if err != nil {
		return nil, err
	}
	store := &Store{guard: guard, path: path, document: Document{SchemaVersion: 1, Datasets: []DatasetEntry{}}}
	if data, err := os.ReadFile(path); err == nil {
		if err := json.Unmarshal(data, &store.document); err != nil {
			return nil, fmt.Errorf("decode catalog: %w", err)
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	return store, nil
}

func (s *Store) Upsert(meta DatasetMeta, metaPath string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	found := false
	for index := range s.document.Datasets {
		if s.document.Datasets[index].DatasetID == meta.DatasetID {
			if s.document.Datasets[index].ActiveRevision == meta.DataRevision && s.document.Datasets[index].MetaPath == metaPath && s.document.Datasets[index].Status == "ready" {
				return nil
			}
			s.document.Datasets[index] = DatasetEntry{meta.DatasetID, meta.DataRevision, metaPath, "ready"}
			found = true
			break
		}
	}
	if !found {
		s.document.Datasets = append(s.document.Datasets, DatasetEntry{meta.DatasetID, meta.DataRevision, metaPath, "ready"})
	}
	sort.Slice(s.document.Datasets, func(i, j int) bool { return s.document.Datasets[i].DatasetID < s.document.Datasets[j].DatasetID })
	s.document.CatalogRevision++
	s.document.UpdatedAt = time.Now().UTC()
	data, err := json.MarshalIndent(s.document, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return storage.AtomicWriteFile(s.path, data, 0o640)
}

func (s *Store) List() (Document, []DatasetMeta, error) {
	s.mu.RLock()
	document := s.document
	document.Datasets = append([]DatasetEntry(nil), s.document.Datasets...)
	s.mu.RUnlock()
	metas := make([]DatasetMeta, 0, len(document.Datasets))
	for _, entry := range document.Datasets {
		meta, err := s.readReadyMeta(entry)
		if err != nil {
			continue
		}
		metas = append(metas, meta)
	}
	return document, metas, nil
}

func (s *Store) Get(datasetID, revision string) (DatasetMeta, error) {
	s.mu.RLock()
	var entry *DatasetEntry
	for index := range s.document.Datasets {
		candidate := s.document.Datasets[index]
		if candidate.DatasetID == datasetID && (revision == "" || candidate.ActiveRevision == revision) {
			entry = &candidate
			break
		}
	}
	s.mu.RUnlock()
	if entry == nil {
		return DatasetMeta{}, ErrNotFound
	}
	return s.readReadyMeta(*entry)
}

func (s *Store) readReadyMeta(entry DatasetEntry) (DatasetMeta, error) {
	path, err := s.guard.Resolve(entry.MetaPath)
	if err != nil {
		return DatasetMeta{}, err
	}
	successPath := filepath.Join(filepath.Dir(path), "_SUCCESS")
	if _, err := os.Stat(successPath); err != nil {
		return DatasetMeta{}, fmt.Errorf("dataset is incomplete: %w", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return DatasetMeta{}, err
	}
	var meta DatasetMeta
	if err := json.Unmarshal(data, &meta); err != nil {
		return DatasetMeta{}, err
	}
	return meta, nil
}
