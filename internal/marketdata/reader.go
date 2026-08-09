package marketdata

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"sync"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/importer"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrInvalidRange     = errors.New("invalid bar range")
	ErrRevisionMismatch = errors.New("data revision mismatch")
)

type Config struct {
	InitialBars       int
	PrefetchBars      int
	MaxBarsPerRequest int
	MaxCachedDatasets int
	BeginTimestampUTC *int64
	EndTimestampUTC   *int64
}

type Query struct {
	DatasetID      string
	DataRevision   string
	GenerationID   string
	Tail           *int
	BeforeBarIndex *int64
	Limit          int
}

type BarColumns struct {
	BarIndex     []int64  `json:"bar_index"`
	TimestampUTC []int64  `json:"timestamp_utc"`
	OpenI64      []int64  `json:"open_i64"`
	HighI64      []int64  `json:"high_i64"`
	LowI64       []int64  `json:"low_i64"`
	CloseI64     []int64  `json:"close_i64"`
	Volume       []int64  `json:"volume"`
	OpenInterest []*int64 `json:"open_interest"`
}

type Coverage struct {
	FirstBarIndex int64 `json:"first_bar_index"`
	LastBarIndex  int64 `json:"last_bar_index"`
}

type Response struct {
	DatasetID     string     `json:"dataset_id"`
	DataRevision  string     `json:"data_revision"`
	GenerationID  string     `json:"generation_id"`
	PriceScale    int64      `json:"price_scale"`
	Coverage      Coverage   `json:"coverage"`
	HasMoreBefore bool       `json:"has_more_before"`
	Checksum      string     `json:"checksum"`
	Bars          BarColumns `json:"bars"`
}

type Reader struct {
	guard   *storage.PathGuard
	catalog *catalog.Store
	config  Config
	mu      sync.Mutex
	cache   map[string][]importer.Bar
	order   []string
}

func NewReader(guard *storage.PathGuard, store *catalog.Store, config Config) *Reader {
	if config.MaxCachedDatasets < 1 {
		config.MaxCachedDatasets = 8
	}
	return &Reader{guard: guard, catalog: store, config: config, cache: make(map[string][]importer.Bar)}
}

func (r *Reader) Read(ctx context.Context, query Query) (Response, error) {
	if query.DatasetID == "" || query.DataRevision == "" || query.GenerationID == "" || query.Tail != nil && query.BeforeBarIndex != nil {
		return Response{}, ErrInvalidRange
	}
	meta, err := r.catalog.Get(query.DatasetID, "")
	if err != nil {
		return Response{}, err
	}
	if meta.DataRevision != query.DataRevision {
		return Response{}, ErrRevisionMismatch
	}
	count, err := r.requestCount(query)
	if err != nil {
		return Response{}, err
	}
	if err := ctx.Err(); err != nil {
		return Response{}, err
	}
	bars, err := r.load(meta)
	if err != nil {
		return Response{}, err
	}
	bars = filterByTimestamp(bars, r.config.BeginTimestampUTC, r.config.EndTimestampUTC)
	start, end := rangeBounds(bars, query.BeforeBarIndex, count)
	selected := bars[start:end]
	columns := columns(selected)
	checksum, err := checksum(columns)
	if err != nil {
		return Response{}, err
	}
	coverage := Coverage{}
	if len(selected) > 0 {
		coverage.FirstBarIndex = selected[0].BarIndex
		coverage.LastBarIndex = selected[len(selected)-1].BarIndex
	}
	return Response{
		DatasetID: query.DatasetID, DataRevision: query.DataRevision, GenerationID: query.GenerationID,
		PriceScale: meta.Price.PriceScale, Coverage: coverage, HasMoreBefore: start > 0, Checksum: checksum, Bars: columns,
	}, nil
}

func (r *Reader) requestCount(query Query) (int, error) {
	count := r.config.InitialBars
	if query.BeforeBarIndex != nil {
		if *query.BeforeBarIndex < 1 || query.Tail != nil {
			return 0, ErrInvalidRange
		}
		count = r.config.PrefetchBars
		if query.Limit > 0 {
			count = query.Limit
		}
	} else if query.Tail != nil {
		count = *query.Tail
		if query.Limit != 0 {
			return 0, ErrInvalidRange
		}
	} else if query.Limit != 0 {
		return 0, ErrInvalidRange
	}
	if count < 1 || count > r.config.MaxBarsPerRequest {
		return 0, ErrInvalidRange
	}
	return count, nil
}

func rangeBounds(bars []importer.Bar, before *int64, count int) (int, int) {
	end := len(bars)
	if before != nil {
		end = sort.Search(len(bars), func(index int) bool {
			return bars[index].BarIndex >= *before
		})
	}
	if end < 0 {
		end = 0
	}
	start := end - count
	if start < 0 {
		start = 0
	}
	return start, end
}

func filterByTimestamp(bars []importer.Bar, begin, end *int64) []importer.Bar {
	start := 0
	if begin != nil {
		start = sort.Search(len(bars), func(index int) bool {
			return bars[index].TimestampUTC >= *begin
		})
	}
	stop := len(bars)
	if end != nil {
		stop = sort.Search(len(bars), func(index int) bool {
			return bars[index].TimestampUTC > *end
		})
	}
	if start > stop {
		return nil
	}
	return bars[start:stop]
}

func (r *Reader) load(meta catalog.DatasetMeta) ([]importer.Bar, error) {
	key := meta.DatasetID + "@" + meta.DataRevision
	r.mu.Lock()
	defer r.mu.Unlock()
	if bars, ok := r.cache[key]; ok {
		r.touch(key)
		return bars, nil
	}
	var relative string
	for _, file := range meta.Files {
		if file.Role == "bars" {
			relative = file.Path
			break
		}
	}
	if relative == "" {
		return nil, fmt.Errorf("dataset metadata has no bars file")
	}
	path, err := r.guard.Resolve(filepath.ToSlash(relative))
	if err != nil {
		return nil, err
	}
	bars, err := parquet.ReadFile[importer.Bar](path)
	if err != nil {
		return nil, fmt.Errorf("read bars parquet: %w", err)
	}
	if int64(len(bars)) != meta.Coverage.BarCount {
		return nil, fmt.Errorf("bars row count does not match metadata")
	}
	for index := range bars {
		if bars[index].BarIndex != int64(index) || index > 0 && bars[index].TimestampUTC <= bars[index-1].TimestampUTC {
			return nil, fmt.Errorf("bars parquet is not strictly ordered")
		}
	}
	r.cache[key] = bars
	r.order = append(r.order, key)
	for len(r.order) > r.config.MaxCachedDatasets {
		delete(r.cache, r.order[0])
		r.order = r.order[1:]
	}
	return bars, nil
}

func (r *Reader) touch(key string) {
	for index, candidate := range r.order {
		if candidate == key {
			copy(r.order[index:], r.order[index+1:])
			r.order[len(r.order)-1] = key
			return
		}
	}
}

func columns(bars []importer.Bar) BarColumns {
	result := BarColumns{
		BarIndex: make([]int64, len(bars)), TimestampUTC: make([]int64, len(bars)), OpenI64: make([]int64, len(bars)),
		HighI64: make([]int64, len(bars)), LowI64: make([]int64, len(bars)), CloseI64: make([]int64, len(bars)),
		Volume: make([]int64, len(bars)), OpenInterest: make([]*int64, len(bars)),
	}
	for index, bar := range bars {
		result.BarIndex[index], result.TimestampUTC[index] = bar.BarIndex, bar.TimestampUTC
		result.OpenI64[index], result.HighI64[index], result.LowI64[index], result.CloseI64[index] = bar.OpenI64, bar.HighI64, bar.LowI64, bar.CloseI64
		result.Volume[index], result.OpenInterest[index] = bar.Volume, bar.OpenInterest
	}
	return result
}

func checksum(columns BarColumns) (string, error) {
	data, err := json.Marshal(columns)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}
