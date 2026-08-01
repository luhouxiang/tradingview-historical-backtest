package importer

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	appconfig "github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrSourceNotFound = errors.New("source file not found")
	ErrSourceChanged  = errors.New("source file changed after scan")
)

type SourceFile struct {
	SourceFileID string         `json:"source_file_id"`
	Path         string         `json:"path"`
	Status       string         `json:"status"`
	SHA256       string         `json:"sha256"`
	SizeBytes    int64          `json:"size_bytes"`
	Detected     map[string]any `json:"detected,omitempty"`
	Issues       []QualityIssue `json:"issues"`
}

type ImportRequest struct {
	SourceFileID       string         `json:"source_file_id"`
	ImporterID         string         `json:"importer_id"`
	Exchange           string         `json:"exchange"`
	Instrument         string         `json:"instrument"`
	Timeframe          string         `json:"timeframe"`
	DateSemantics      string         `json:"date_semantics"`
	Timezone           string         `json:"timezone"`
	TimestampSemantics string         `json:"timestamp_semantics"`
	Options            map[string]any `json:"options,omitempty"`
}

type Service struct {
	mu      sync.RWMutex
	errorMu sync.Mutex
	guard   *storage.PathGuard
	catalog *catalog.Store
	config  appconfig.Config
	logger  *logx.Logger
	sources map[string]SourceFile
}

func NewService(guard *storage.PathGuard, store *catalog.Store, cfg appconfig.Config, logger *logx.Logger) *Service {
	return &Service{guard: guard, catalog: store, config: cfg, logger: logger, sources: make(map[string]SourceFile)}
}

func (s *Service) Scan(ctx context.Context) ([]SourceFile, error) {
	s.logger.Info("source.scan.started", "source file scan started", nil)
	historyPath, err := s.guard.Resolve(s.config.Import.SourceDirectory)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(historyPath, 0o750); err != nil {
		return nil, err
	}
	runtimeConfig, configErr := loadRuntimeConfig(s.guard)
	discovered := make([]SourceFile, 0)
	err = filepath.WalkDir(historyPath, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 || !strings.EqualFold(filepath.Ext(entry.Name()), ".txt") {
			return nil
		}
		relative, err := s.guard.Relative(path)
		if err != nil {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		detection, detectErr := DetectTdx(data)
		hash := hashBytes(data)
		source := SourceFile{
			SourceFileID: sourceID(relative, hash), Path: relative, Status: "rejected", SHA256: hash,
			SizeBytes: int64(len(data)), Detected: map[string]any{}, Issues: []QualityIssue{},
		}
		if detectErr != nil {
			source.Issues = append(source.Issues, QualityIssue{Level: "ERROR", Code: "UNRECOGNIZED_SOURCE", Message: detectErr.Error()})
			s.logger.Warn("source.file.rejected", "source file rejected", map[string]any{"reason": detectErr.Error()})
		} else {
			source.Status = "needs_mapping"
			source.Detected = map[string]any{
				"symbol": detection.Symbol, "display_name": detection.DisplayName, "timeframe": detection.Timeframe,
				"encoding": detection.Encoding, "format": detection.Format, "title": detection.Title,
			}
			if configErr != nil {
				source.Issues = append(source.Issues, QualityIssue{Level: "ERROR", Code: "IMPORT_CONFIG_UNAVAILABLE", Message: configErr.Error()})
			} else if instrument, ok := findInstrument(runtimeConfig.instruments, detection.Symbol); ok {
				source.Status = "importable"
				source.Detected["exchange"] = instrument.Exchange
				source.Detected["timezone"] = instrument.Timezone
				source.Detected["date_semantics"] = "trading_day"
				source.Detected["timestamp_semantics"] = "bar_end"
			}
			s.logger.Info("source.file.discovered", "source file discovered", map[string]any{"source_file_id": source.SourceFileID, "status": source.Status})
		}
		discovered = append(discovered, source)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(discovered, func(i, j int) bool { return discovered[i].Path < discovered[j].Path })
	s.mu.Lock()
	s.sources = make(map[string]SourceFile, len(discovered))
	for _, source := range discovered {
		s.sources[source.SourceFileID] = source
	}
	s.mu.Unlock()
	s.logger.Info("source.scan.completed", "source file scan completed", map[string]any{"source_count": len(discovered)})
	return discovered, nil
}

func (s *Service) SourceFiles() []SourceFile {
	s.mu.RLock()
	defer s.mu.RUnlock()
	items := make([]SourceFile, 0, len(s.sources))
	for _, source := range s.sources {
		items = append(items, source)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Path < items[j].Path })
	return items
}

func (s *Service) Import(ctx context.Context, request ImportRequest, progress func(float64)) (catalog.DatasetMeta, bool, error) {
	if request.ImporterID != AdapterID {
		return catalog.DatasetMeta{}, false, fmt.Errorf("unsupported importer %q", request.ImporterID)
	}
	s.mu.RLock()
	source, ok := s.sources[request.SourceFileID]
	s.mu.RUnlock()
	if !ok {
		return catalog.DatasetMeta{}, false, ErrSourceNotFound
	}
	path, err := s.guard.Resolve(source.Path)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	before, err := os.ReadFile(path)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if hashBytes(before) != source.SHA256 {
		return catalog.DatasetMeta{}, false, ErrSourceChanged
	}
	progress(0.1)
	runtimeConfig, err := loadRuntimeConfig(s.guard)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	instrument, ok := runtimeConfig.instrument(request.Exchange, request.Instrument)
	if !ok {
		return catalog.DatasetMeta{}, false, fmt.Errorf("instrument mapping not found")
	}
	session, ok := runtimeConfig.sessions[instrument.SessionTemplateID]
	if !ok {
		return catalog.DatasetMeta{}, false, fmt.Errorf("session template %q not found", instrument.SessionTemplateID)
	}
	options := ImportOptions{
		DateSemantics: request.DateSemantics, TimestampSemantics: request.TimestampSemantics, Timezone: request.Timezone,
		FailOnDuplicate: s.config.Import.FailOnDuplicateTimestamp, KeepZeroVolumeBars: s.config.Import.KeepZeroVolumeBars,
		FillMissingBars: s.config.Import.FillMissingBars,
	}
	if options.TimestampSemantics == "" {
		options.TimestampSemantics = "bar_end"
	}
	request.TimestampSemantics = options.TimestampSemantics
	canonical, optionsHash, err := canonicalOptions(options)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	result, err := ParseTdx(before, source.Path, source.SHA256, instrument, session, runtimeConfig.calendar, options)
	if err != nil {
		if qualityErr := new(QualityError); errors.As(err, &qualityErr) {
			_ = s.appendImportError(source, qualityErr.Report)
		}
		return catalog.DatasetMeta{}, false, err
	}
	if result.Detection.Symbol != request.Instrument || result.Detection.Timeframe != request.Timeframe {
		return catalog.DatasetMeta{}, false, fmt.Errorf("requested mapping does not match detected title")
	}
	after, err := os.ReadFile(path)
	if err != nil || hashBytes(after) != source.SHA256 {
		return catalog.DatasetMeta{}, false, ErrSourceChanged
	}
	progress(0.35)
	revision := dataRevision(before, canonical, runtimeConfig, AdapterID, AdapterVersion)
	datasetID := request.Exchange + "." + request.Instrument + "." + request.Timeframe
	revisionShort := strings.TrimPrefix(revision, "sha256:")[:12]
	targetRelative := filepath.ToSlash(filepath.Join("normalized", datasetID, revisionShort))
	target, err := s.guard.Resolve(targetRelative)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if meta, reusable := s.reusable(target, datasetID, revision); reusable {
		metaPath := filepath.ToSlash(filepath.Join(targetRelative, "meta.json"))
		if err := s.catalog.Upsert(meta, metaPath); err != nil {
			return catalog.DatasetMeta{}, false, err
		}
		s.markImported(source.SourceFileID)
		return meta, true, nil
	}
	if _, err := os.Stat(target); err == nil {
		if err := s.quarantine(target, datasetID, revisionShort); err != nil {
			return catalog.DatasetMeta{}, false, err
		}
	} else if !os.IsNotExist(err) {
		return catalog.DatasetMeta{}, false, err
	}
	tmpRoot, err := s.guard.Resolve("tmp")
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if err := os.MkdirAll(tmpRoot, 0o750); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	temporary, err := os.MkdirTemp(tmpRoot, "import-*")
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	committed := false
	defer func() {
		if !committed {
			_ = os.RemoveAll(temporary)
		}
	}()
	qualityBytes, err := json.MarshalIndent(result.Quality, "", "  ")
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	qualityBytes = append(qualityBytes, '\n')
	qualityPath := filepath.Join(temporary, "quality.json")
	if err := os.WriteFile(qualityPath, qualityBytes, 0o640); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	barsPath := filepath.Join(temporary, "bars.parquet")
	if err := writeBars(barsPath, result.Bars, datasetID, revision, request, instrument, s.config.Storage.ParquetRowGroupSize); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	progress(0.75)
	barsFile, err := fileMeta("bars", filepath.ToSlash(filepath.Join(targetRelative, "bars.parquet")), barsPath)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	qualityFile, err := fileMeta("quality", filepath.ToSlash(filepath.Join(targetRelative, "quality.json")), qualityPath)
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	meta := buildMeta(datasetID, revision, source, request, instrument, result, runtimeConfig, optionsHash, []catalog.FileMeta{barsFile, qualityFile})
	metaBytes, err := json.MarshalIndent(meta, "", "  ")
	if err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	metaBytes = append(metaBytes, '\n')
	if err := os.WriteFile(filepath.Join(temporary, "meta.json"), metaBytes, 0o640); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if err := ctx.Err(); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	if err := os.Rename(temporary, target); err != nil {
		return catalog.DatasetMeta{}, false, fmt.Errorf("commit dataset directory: %w", err)
	}
	committed = true
	if err := storage.AtomicWriteFile(filepath.Join(target, "_SUCCESS"), []byte(revision+"\n"), 0o640); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	metaPath := filepath.ToSlash(filepath.Join(targetRelative, "meta.json"))
	if err := s.catalog.Upsert(meta, metaPath); err != nil {
		return catalog.DatasetMeta{}, false, err
	}
	s.markImported(source.SourceFileID)
	progress(1)
	s.logger.Info("dataset.import.completed", "dataset import completed", map[string]any{"dataset_id": datasetID, "data_revision": revision, "bar_count": len(result.Bars)})
	return meta, false, nil
}

func (s *Service) markImported(sourceFileID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if source, ok := s.sources[sourceFileID]; ok {
		source.Status = "imported"
		s.sources[sourceFileID] = source
	}
}

func (s *Service) ListDatasets() (catalog.Document, []catalog.DatasetMeta, error) {
	return s.catalog.List()
}
func (s *Service) GetDataset(datasetID, revision string) (catalog.DatasetMeta, error) {
	return s.catalog.Get(datasetID, revision)
}

func findInstrument(instruments []InstrumentConfig, symbol string) (InstrumentConfig, bool) {
	for _, instrument := range instruments {
		if instrument.pattern.MatchString(symbol) {
			return instrument, true
		}
	}
	return InstrumentConfig{}, false
}

func sourceID(path, hash string) string {
	sum := sha256.Sum256([]byte(path + "\x00" + hash))
	return "src-" + hex.EncodeToString(sum[:12])
}

func dataRevision(source, options []byte, config runtimeConfig, importerID, importerVersion string) string {
	hasher := sha256.New()
	for _, value := range [][]byte{source, []byte(importerID), []byte(importerVersion), options, []byte(config.instrumentHash), []byte(config.sessionHash), []byte(config.calendarHash)} {
		_ = binary.Write(hasher, binary.BigEndian, uint64(len(value)))
		_, _ = hasher.Write(value)
	}
	return "sha256:" + hex.EncodeToString(hasher.Sum(nil))
}

func writeBars(path string, bars []Bar, datasetID, revision string, request ImportRequest, instrument InstrumentConfig, rowGroupSize int) error {
	options := []parquet.WriterOption{
		parquet.MaxRowsPerRowGroup(int64(rowGroupSize)),
		parquet.KeyValueMetadata("schema_version", "1"),
		parquet.KeyValueMetadata("dataset_id", datasetID),
		parquet.KeyValueMetadata("data_revision", revision),
		parquet.KeyValueMetadata("timezone", request.Timezone),
		parquet.KeyValueMetadata("timestamp_semantics", request.TimestampSemantics),
		parquet.KeyValueMetadata("price_decimals", strconv.Itoa(instrument.PriceDecimals)),
		parquet.KeyValueMetadata("price_scale", strconv.FormatInt(instrument.PriceScale, 10)),
		parquet.KeyValueMetadata("importer_id", AdapterID),
		parquet.KeyValueMetadata("importer_version", AdapterVersion),
	}
	return parquet.WriteFile(path, bars, options...)
}

func fileMeta(role, relative, path string) (catalog.FileMeta, error) {
	file, err := os.Open(path)
	if err != nil {
		return catalog.FileMeta{}, err
	}
	defer file.Close()
	hasher := sha256.New()
	size, err := io.Copy(hasher, file)
	if err != nil {
		return catalog.FileMeta{}, err
	}
	return catalog.FileMeta{Role: role, Path: relative, SHA256: "sha256:" + hex.EncodeToString(hasher.Sum(nil)), SizeBytes: size}, nil
}

func buildMeta(datasetID, revision string, source SourceFile, request ImportRequest, instrument InstrumentConfig, result ParseResult, runtimeConfig runtimeConfig, optionsHash string, files []catalog.FileMeta) catalog.DatasetMeta {
	first, last := result.Bars[0], result.Bars[len(result.Bars)-1]
	counts := result.Quality.Counts
	return catalog.DatasetMeta{
		SchemaVersion: 1, DatasetID: datasetID, DataRevision: revision,
		Instrument: catalog.InstrumentMeta{Exchange: request.Exchange, Symbol: request.Instrument, Product: instrument.Product, DisplayName: result.Detection.DisplayName},
		Timeframe:  request.Timeframe,
		Source:     catalog.SourceMeta{Path: source.Path, SHA256: source.SHA256, Encoding: result.Detection.Encoding, Format: AdapterID, Title: result.Detection.Title, TimestampSemantics: request.TimestampSemantics},
		Time:       catalog.TimeMeta{Timezone: request.Timezone, DateSemantics: request.DateSemantics, TradingCalendarHash: runtimeConfig.calendarHash, SessionConfigHash: runtimeConfig.sessionHash},
		Price:      catalog.PriceMeta{PriceDecimals: instrument.PriceDecimals, PriceScale: instrument.PriceScale, TickSizeI64: instrument.TickSizeI64},
		Coverage:   catalog.CoverageMeta{BarCount: int64(len(result.Bars)), FirstBarIndex: first.BarIndex, LastBarIndex: last.BarIndex, FirstTimestampUTC: first.TimestampUTC, LastTimestampUTC: last.TimestampUTC, FirstTradingDay: fromDate32(first.TradingDay), LastTradingDay: fromDate32(last.TradingDay)},
		Importer:   catalog.ImporterMeta{ID: AdapterID, Version: AdapterVersion, OptionsHash: optionsHash},
		Quality:    catalog.QualityCounts{DuplicateCount: counts["duplicate_count"], InvalidOHLCCount: counts["invalid_ohlc_count"], ZeroVolumeCount: counts["zero_volume_count"], GapCount: counts["gap_count"], WarningCount: counts["warning_count"], ErrorCount: counts["error_count"]},
		Files:      files, CreatedAt: time.Now().UTC(),
	}
}

func fromDate32(days int32) string {
	return time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC).AddDate(0, 0, int(days)).Format(time.DateOnly)
}

func (s *Service) reusable(target, datasetID, revision string) (catalog.DatasetMeta, bool) {
	if _, err := os.Stat(filepath.Join(target, "_SUCCESS")); err != nil {
		return catalog.DatasetMeta{}, false
	}
	data, err := os.ReadFile(filepath.Join(target, "meta.json"))
	if err != nil {
		return catalog.DatasetMeta{}, false
	}
	var meta catalog.DatasetMeta
	if json.Unmarshal(data, &meta) != nil || meta.DatasetID != datasetID || meta.DataRevision != revision {
		return catalog.DatasetMeta{}, false
	}
	return meta, true
}

func (s *Service) quarantine(target, datasetID, revisionShort string) error {
	tmp, err := s.guard.Resolve("tmp/interrupted")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(tmp, 0o750); err != nil {
		return err
	}
	random := make([]byte, 4)
	_, _ = rand.Read(random)
	name := strings.ReplaceAll(datasetID, ".", "_") + "-" + revisionShort + "-" + hex.EncodeToString(random)
	return os.Rename(target, filepath.Join(tmp, name))
}

func (s *Service) appendImportError(source SourceFile, report QualityReport) error {
	s.errorMu.Lock()
	defer s.errorMu.Unlock()
	path, err := s.guard.Resolve("catalog/import-errors.ndjson")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	record := map[string]any{"timestamp": time.Now().UTC(), "source_file_id": source.SourceFileID, "source_path": source.Path, "quality": report}
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.Write(append(data, '\n'))
	return err
}
