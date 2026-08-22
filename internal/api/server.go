package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/comparison"
	"github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/importer"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
	"github.com/tvbt/tradingview-historical-backtest/internal/marketdata"
	"github.com/tvbt/tradingview-historical-backtest/internal/optimization"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/replay"
	"github.com/tvbt/tradingview-historical-backtest/internal/workspace"
)

type contextKey string

const (
	requestIDKey contextKey = "request_id"
	traceIDKey   contextKey = "trace_id"
)

type Server struct {
	contractVersion string
	python          *pythonclient.Client
	logger          *logx.Logger
	vueLogger       *logx.Logger
	maxLogBody      int64
	maxLogEvents    int
	datasets        DatasetService
	bars            BarReader
	jobs            *jobs.Manager
	calculations    *calculation.Service
	replays         *replay.Service
	backtests       *backtest.Service
	optimization    *optimization.Service
	comparisons     *comparison.Service
	workspace       *workspace.Store
	handler         http.Handler
}

type DatasetService interface {
	Scan(context.Context) ([]importer.SourceFile, error)
	SourceFiles() []importer.SourceFile
	Import(context.Context, importer.ImportRequest, func(float64)) (catalog.DatasetMeta, bool, error)
	ListDatasets() (catalog.Document, []catalog.DatasetMeta, error)
	GetDataset(string, string) (catalog.DatasetMeta, error)
}

type BarReader interface {
	Read(context.Context, marketdata.Query) (marketdata.Response, error)
}

type Option func(*Server)

func WithDatasets(datasets DatasetService, manager *jobs.Manager) Option {
	return func(server *Server) { server.datasets, server.jobs = datasets, manager }
}

func WithBarReader(reader BarReader) Option {
	return func(server *Server) { server.bars = reader }
}

func WithCalculations(service *calculation.Service) Option {
	return func(server *Server) { server.calculations = service }
}

func WithReplays(service *replay.Service) Option {
	return func(server *Server) { server.replays = service }
}

func WithBacktests(service *backtest.Service) Option {
	return func(server *Server) { server.backtests = service }
}

func WithOptimization(service *optimization.Service) Option {
	return func(server *Server) { server.optimization = service }
}

func WithComparisons(service *comparison.Service) Option {
	return func(server *Server) { server.comparisons = service }
}

func WithWorkspace(store *workspace.Store) Option {
	return func(server *Server) { server.workspace = store }
}

func NewServer(cfg config.Config, python *pythonclient.Client, logger, vueLogger *logx.Logger, options ...Option) *Server {
	s := &Server{
		contractVersion: cfg.App.ContractVersion,
		python:          python,
		logger:          logger,
		vueLogger:       vueLogger,
		maxLogBody:      cfg.Logging.VueRequestMaxBytes,
		maxLogEvents:    cfg.Logging.VueBatchMaxEvents,
	}
	for _, option := range options {
		option(s)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/health", s.health)
	mux.HandleFunc("POST /api/v1/datasets/scan", s.scanDatasets)
	mux.HandleFunc("GET /api/v1/source-files", s.sourceFiles)
	mux.HandleFunc("POST /api/v1/datasets/import", s.importDataset)
	mux.HandleFunc("GET /api/v1/datasets", s.listDatasets)
	mux.HandleFunc("GET /api/v1/datasets/{dataset_id}/bars", s.getBars)
	mux.HandleFunc("GET /api/v1/datasets/{dataset_id}", s.getDataset)
	mux.HandleFunc("GET /api/v1/jobs/{job_id}", s.getJob)
	mux.HandleFunc("POST /api/v1/jobs/{job_id}/cancel", s.cancelJob)
	mux.HandleFunc("GET /api/v1/algorithms", s.algorithms)
	mux.HandleFunc("POST /api/v1/calculations", s.createCalculation)
	mux.HandleFunc("GET /api/v1/calculations/{job_id}", s.getCalculation)
	mux.HandleFunc("POST /api/v1/calculations/{job_id}/cancel", s.cancelCalculation)
	mux.HandleFunc("GET /api/v1/calculations/{job_id}/results", s.calculationResults)
	mux.HandleFunc("POST /api/v1/replays", s.createReplay)
	mux.HandleFunc("GET /api/v1/replays/{replay_id}", s.getReplay)
	mux.HandleFunc("GET /api/v1/replays/{replay_id}/events", s.getReplayEvents)
	mux.HandleFunc("POST /api/v1/backtests", s.createBacktest)
	mux.HandleFunc("GET /api/v1/backtests/{run_id}", s.getBacktest)
	mux.HandleFunc("POST /api/v1/backtests/{run_id}/cancel", s.cancelBacktest)
	mux.HandleFunc("GET /api/v1/backtests/{run_id}/summary", s.getBacktestSummary)
	mux.HandleFunc("GET /api/v1/backtests/{run_id}/trades", s.getBacktestTrades)
	mux.HandleFunc("GET /api/v1/backtests/{run_id}/equity", s.getBacktestEquity)
	mux.HandleFunc("GET /api/v1/backtests/{run_id}/chart-events", s.getBacktestChartEvents)
	mux.HandleFunc("POST /api/v1/strategy-comparisons", s.createStrategyComparison)
	mux.HandleFunc("GET /api/v1/strategy-comparisons", s.listStrategyComparisons)
	mux.HandleFunc("GET /api/v1/strategy-comparisons/{comparison_id}", s.getStrategyComparison)
	mux.HandleFunc("POST /api/v1/strategy-comparisons/{comparison_id}/cancel", s.cancelStrategyComparison)
	mux.HandleFunc("GET /api/v1/strategy-comparisons/{comparison_id}/results", s.getStrategyComparisonResults)
	mux.HandleFunc("POST /api/v1/studies", s.createStudy)
	mux.HandleFunc("GET /api/v1/studies/{study_id}", s.getStudy)
	mux.HandleFunc("POST /api/v1/studies/{study_id}/cancel", s.cancelStudy)
	mux.HandleFunc("GET /api/v1/studies/{study_id}/evaluations", s.getStudyEvaluations)
	mux.HandleFunc("GET /api/v1/workspaces/{profile_id}/layouts/{layout_id}", s.getLayout)
	mux.HandleFunc("PUT /api/v1/workspaces/{profile_id}/layouts/{layout_id}", s.putLayout)
	mux.HandleFunc("GET /api/v1/workspaces/{profile_id}/strategy-source-config", s.getStrategySourceConfig)
	mux.HandleFunc("PUT /api/v1/workspaces/{profile_id}/strategy-source-config", s.putStrategySourceConfig)
	mux.HandleFunc("GET /api/v1/workspaces/{profile_id}/drawings/{layout_id}/{dataset_id}", s.getDrawings)
	mux.HandleFunc("PUT /api/v1/workspaces/{profile_id}/drawings/{layout_id}/{dataset_id}", s.putDrawings)
	mux.HandleFunc("POST /api/v1/client-logs", s.clientLogs)
	mux.HandleFunc("/api/v1/", s.notImplemented)
	s.handler = s.requestContext(mux)
	return s
}

func (s *Server) createStudy(w http.ResponseWriter, r *http.Request) {
	if s.optimization == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request optimization.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_STUDY_REQUEST", "Optimization study request is invalid", nil)
		return
	}
	result, err := s.optimization.Submit(r.Context(), requestID(r.Context()), traceID(r.Context()), request)
	switch {
	case errors.Is(err, optimization.ErrInvalidRequest):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_STUDY_PARAMETERS", "Optimization parameters are invalid", nil)
		return
	case errors.Is(err, optimization.ErrInvalidRange):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_STUDY_RANGE", "Training and validation ranges are invalid", nil)
		return
	case errors.Is(err, optimization.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusServiceUnavailable, "STUDY_SUBMIT_FAILED", "Optimization study could not be submitted", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "study_id": result.StudyID, "status": result.Job.Status})
}

func (s *Server) getStudy(w http.ResponseWriter, r *http.Request) {
	if s.optimization == nil {
		s.notImplemented(w, r)
		return
	}
	job, manifest, ok := s.optimization.Status(r.PathValue("study_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "STUDY_NOT_FOUND", "Optimization study was not found", nil)
		return
	}
	payload := map[string]any{"request_id": requestID(r.Context()), "study_id": r.PathValue("study_id"), "status": job.Status, "progress": job.Progress}
	if job.ResultRef != "" {
		payload["result_ref"] = job.ResultRef
	}
	if manifest != nil {
		payload["manifest"] = manifest
	}
	if job.Error != nil {
		payload["error"] = job.Error
	}
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) cancelStudy(w http.ResponseWriter, r *http.Request) {
	if s.optimization == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.optimization.Cancel(r.PathValue("study_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "STUDY_NOT_FOUND", "Optimization study was not found", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "study_id": r.PathValue("study_id"), "status": job.Status, "progress": job.Progress})
}

func (s *Server) getStudyEvaluations(w http.ResponseWriter, r *http.Request) {
	if s.optimization == nil {
		s.notImplemented(w, r)
		return
	}
	evaluations, stability, err := s.optimization.Evaluations(r.PathValue("study_id"))
	switch {
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "STUDY_NOT_FOUND", "Optimization study was not found", nil)
		return
	case errors.Is(err, optimization.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "STUDY_NOT_READY", "Optimization study is not completed", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "STUDY_RESULTS_READ_FAILED", "Optimization results could not be read", nil)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "study_id": r.PathValue("study_id"), "evaluations": evaluations, "stability": stability})
}

func (s *Server) createStrategyComparison(w http.ResponseWriter, r *http.Request) {
	if s.comparisons == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request comparison.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_COMPARISON_REQUEST", "Strategy comparison request is invalid", nil)
		return
	}
	result, err := s.comparisons.Submit(r.Context(), requestID(r.Context()), traceID(r.Context()), request)
	switch {
	case errors.Is(err, comparison.ErrInvalidRequest):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_COMPARISON_PARAMETERS", "Strategy comparison parameters are invalid", nil)
		return
	case errors.Is(err, comparison.ErrInvalidRange):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_COMPARISON_RANGE", "Strategy comparison range is invalid", nil)
		return
	case errors.Is(err, comparison.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusServiceUnavailable, "COMPARISON_SUBMIT_FAILED", "Strategy comparison could not be submitted", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "comparison_id": result.ComparisonID, "status": result.Job.Status})
}

func (s *Server) getStrategyComparison(w http.ResponseWriter, r *http.Request) {
	if s.comparisons == nil {
		s.notImplemented(w, r)
		return
	}
	job, detail, manifest, ok := s.comparisons.Status(r.PathValue("comparison_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "COMPARISON_NOT_FOUND", "Strategy comparison was not found", nil)
		return
	}
	payload := map[string]any{
		"request_id": requestID(r.Context()), "comparison_id": r.PathValue("comparison_id"),
		"status": job.Status, "progress": job.Progress, "total_count": detail.TotalCount,
		"completed_count": detail.CompletedCount, "failed_count": detail.FailedCount,
		"current_algorithm_id": detail.CurrentAlgorithmID,
	}
	if job.ResultRef != "" {
		payload["result_ref"] = job.ResultRef
	}
	if manifest != nil {
		payload["manifest"] = manifest
	}
	if job.Error != nil {
		payload["error"] = job.Error
	}
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) cancelStrategyComparison(w http.ResponseWriter, r *http.Request) {
	if s.comparisons == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.comparisons.Cancel(r.PathValue("comparison_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "COMPARISON_NOT_FOUND", "Strategy comparison was not found", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "comparison_id": r.PathValue("comparison_id"), "status": job.Status, "progress": job.Progress})
}

func (s *Server) listStrategyComparisons(w http.ResponseWriter, r *http.Request) {
	if s.comparisons == nil {
		s.notImplemented(w, r)
		return
	}
	items, err := s.comparisons.List(r.URL.Query().Get("dataset_id"))
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, "COMPARISON_LIST_FAILED", "Strategy comparisons could not be listed", nil)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "items": items})
}

func (s *Server) getStrategyComparisonResults(w http.ResponseWriter, r *http.Request) {
	if s.comparisons == nil {
		s.notImplemented(w, r)
		return
	}
	items, err := s.comparisons.Results(r.PathValue("comparison_id"))
	switch {
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "COMPARISON_NOT_FOUND", "Strategy comparison was not found", nil)
		return
	case errors.Is(err, comparison.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "COMPARISON_NOT_READY", "Strategy comparison is not completed", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "COMPARISON_RESULTS_READ_FAILED", "Strategy comparison results could not be read", nil)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "comparison_id": r.PathValue("comparison_id"), "items": items})
}

func (s *Server) createBacktest(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request backtest.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_BACKTEST_REQUEST", "Backtest request is invalid", nil)
		return
	}
	result, err := s.backtests.Submit(r.Context(), requestID(r.Context()), traceID(r.Context()), r.Header.Get("Idempotency-Key"), request)
	switch {
	case errors.Is(err, backtest.ErrInvalidRequest):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_BACKTEST_PARAMETERS", "Backtest parameters are invalid", nil)
		return
	case errors.Is(err, backtest.ErrInvalidRange):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_BACKTEST_RANGE", "Backtest range is invalid", nil)
		return
	case errors.Is(err, backtest.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusServiceUnavailable, "BACKTEST_SUBMIT_FAILED", "Backtest could not be submitted", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "run_id": result.RunID, "run_signature": result.RunSignature, "status": result.Job.Status})
}

func (s *Server) getBacktest(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	job, signature, manifest, ok := s.backtests.Status(r.PathValue("run_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "Backtest run was not found", nil)
		return
	}
	payload := map[string]any{"request_id": requestID(r.Context()), "run_id": r.PathValue("run_id"), "run_signature": signature, "status": job.Status, "progress": job.Progress}
	if manifest != nil {
		payload["manifest"] = manifest
	}
	if job.Error != nil {
		payload["error"] = job.Error
	}
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) cancelBacktest(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	job, signature, ok := s.backtests.Cancel(r.PathValue("run_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "Backtest run was not found", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "run_id": r.PathValue("run_id"), "run_signature": signature, "status": job.Status, "progress": job.Progress})
}

func (s *Server) getBacktestSummary(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	value, err := s.backtests.Summary(r.PathValue("run_id"))
	if s.writeBacktestReadError(w, r, err) {
		return
	}
	value["request_id"], value["run_id"] = requestID(r.Context()), r.PathValue("run_id")
	writeJSON(w, http.StatusOK, value)
}

func (s *Server) getBacktestTrades(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	rows, next, err := s.backtests.Trades(r.PathValue("run_id"), r.URL.Query().Get("cursor"))
	if s.writeBacktestReadError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "rows": rows, "next_cursor": next})
}

func (s *Server) getBacktestEquity(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	rows, err := s.backtests.Equity(r.PathValue("run_id"))
	if s.writeBacktestReadError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "run_id": r.PathValue("run_id"), "rows": rows})
}

func (s *Server) getBacktestChartEvents(w http.ResponseWriter, r *http.Request) {
	if s.backtests == nil {
		s.notImplemented(w, r)
		return
	}
	events, err := s.backtests.ChartEvents(r.PathValue("run_id"))
	if s.writeBacktestReadError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "run_id": r.PathValue("run_id"), "events": events})
}

func (s *Server) writeBacktestReadError(w http.ResponseWriter, r *http.Request, err error) bool {
	switch {
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "Backtest run was not found", nil)
	case errors.Is(err, backtest.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "RUN_NOT_READY", "Backtest run is not completed", nil)
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "RUN_RESULT_READ_FAILED", "Backtest result could not be read", nil)
	default:
		return false
	}
	return true
}

func (s *Server) createReplay(w http.ResponseWriter, r *http.Request) {
	if s.replays == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request replay.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_REPLAY_REQUEST", "Replay request is invalid", nil)
		return
	}
	result, err := s.replays.Submit(r.Context(), requestID(r.Context()), traceID(r.Context()), request)
	switch {
	case errors.Is(err, replay.ErrInvalidRequest):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_REPLAY_PARAMETERS", "Replay parameters are invalid", nil)
		return
	case errors.Is(err, replay.ErrInvalidRange):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_REPLAY_RANGE", "Replay range is invalid", nil)
		return
	case errors.Is(err, replay.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusServiceUnavailable, "REPLAY_SUBMIT_FAILED", "Replay could not be submitted", nil)
		return
	}
	status := http.StatusAccepted
	if result.CacheHit {
		status = http.StatusOK
	}
	writeJSON(w, status, replayPayload(requestID(r.Context()), result.ReplayID, result.CacheKey, result.Job))
}

func (s *Server) getReplay(w http.ResponseWriter, r *http.Request) {
	if s.replays == nil {
		s.notImplemented(w, r)
		return
	}
	job, key, ok := s.replays.Status(r.PathValue("replay_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "REPLAY_NOT_FOUND", "Replay was not found", nil)
		return
	}
	writeJSON(w, http.StatusOK, replayPayload(requestID(r.Context()), r.PathValue("replay_id"), key, job))
}

func (s *Server) getReplayEvents(w http.ResponseWriter, r *http.Request) {
	if s.replays == nil {
		s.notImplemented(w, r)
		return
	}
	from, fromErr := strconv.ParseInt(r.URL.Query().Get("known_from_bar_index"), 10, 64)
	to, toErr := strconv.ParseInt(r.URL.Query().Get("known_to_bar_index"), 10, 64)
	if fromErr != nil || toErr != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_REPLAY_RANGE", "Replay event range is invalid", nil)
		return
	}
	result, err := s.replays.Events(r.PathValue("replay_id"), from, to)
	switch {
	case errors.Is(err, replay.ErrInvalidRange):
		s.writeError(w, r, http.StatusBadRequest, "INVALID_REPLAY_RANGE", "Replay event range is invalid", nil)
		return
	case errors.Is(err, replay.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "REPLAY_NOT_READY", "Replay is not completed", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "REPLAY_NOT_FOUND", "Replay was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "REPLAY_EVENT_READ_FAILED", "Replay events could not be read", nil)
		return
	}
	data, _ := json.Marshal(result)
	payload := map[string]any{}
	_ = json.Unmarshal(data, &payload)
	payload["request_id"] = requestID(r.Context())
	writeJSON(w, http.StatusOK, payload)
}

func replayPayload(requestID, replayID, cacheKey string, job *jobs.Job) map[string]any {
	payload := map[string]any{"request_id": requestID, "replay_id": replayID, "cache_key": cacheKey, "status": job.Status, "progress": job.Progress}
	if job.ResultRef != "" {
		payload["result_ref"] = job.ResultRef
	}
	if job.Error != nil {
		payload["error"] = job.Error
	}
	return payload
}

func expectedRevision(r *http.Request) (int, error) {
	value, err := strconv.Atoi(r.Header.Get("If-Match"))
	if err != nil || value < 0 {
		return 0, workspace.ErrInvalid
	}
	return value, nil
}

func (s *Server) getLayout(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	document, err := s.workspace.GetLayout(r.PathValue("profile_id"), r.PathValue("layout_id"))
	s.writeWorkspaceResult(w, r, document, err)
}

func (s *Server) putLayout(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	expected, err := expectedRevision(r)
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, "EXPECTED_REVISION_REQUIRED", "If-Match must contain the expected revision", nil)
		return
	}
	defer r.Body.Close()
	var document workspace.Layout
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_LAYOUT", "Layout document is invalid", map[string]any{"reason": err.Error()})
		return
	}
	saved, err := s.workspace.PutLayout(r.PathValue("profile_id"), r.PathValue("layout_id"), expected, document)
	s.writeWorkspaceResult(w, r, saved, err)
}

func (s *Server) getStrategySourceConfig(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	document, err := s.workspace.GetStrategySourceConfig(r.PathValue("profile_id"))
	s.writeWorkspaceResult(w, r, document, err)
}

func (s *Server) putStrategySourceConfig(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	expected, err := expectedRevision(r)
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, "EXPECTED_REVISION_REQUIRED", "If-Match must contain the expected revision", nil)
		return
	}
	defer r.Body.Close()
	var document workspace.StrategySourceConfig
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_STRATEGY_SOURCE_CONFIG", "StrategySource configuration document is invalid", map[string]any{"reason": err.Error()})
		return
	}
	saved, err := s.workspace.PutStrategySourceConfig(r.PathValue("profile_id"), expected, document)
	s.writeWorkspaceResult(w, r, saved, err)
}

func (s *Server) getDrawings(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	document, err := s.workspace.GetDrawings(r.PathValue("profile_id"), r.PathValue("layout_id"), r.PathValue("dataset_id"))
	s.writeWorkspaceResult(w, r, document, err)
}

func (s *Server) putDrawings(w http.ResponseWriter, r *http.Request) {
	if s.workspace == nil {
		s.notImplemented(w, r)
		return
	}
	expected, err := expectedRevision(r)
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, "EXPECTED_REVISION_REQUIRED", "If-Match must contain the expected revision", nil)
		return
	}
	defer r.Body.Close()
	var document workspace.Drawings
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4*1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_DRAWINGS", "Drawing document is invalid", map[string]any{"reason": err.Error()})
		return
	}
	saved, err := s.workspace.PutDrawings(r.PathValue("profile_id"), r.PathValue("layout_id"), r.PathValue("dataset_id"), expected, document)
	s.writeWorkspaceResult(w, r, saved, err)
}

func (s *Server) writeWorkspaceResult(w http.ResponseWriter, r *http.Request, document any, err error) {
	var conflict *workspace.ConflictError
	switch {
	case errors.Is(err, workspace.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "WORKSPACE_NOT_FOUND", "Workspace document was not found", nil)
	case errors.Is(err, workspace.ErrInvalid):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_WORKSPACE_DOCUMENT", "Workspace document is invalid", nil)
	case errors.As(err, &conflict):
		s.writeError(w, r, http.StatusConflict, "WORKSPACE_REVISION_CONFLICT", "Workspace document revision conflicts with the saved revision", map[string]any{"current_revision": conflict.CurrentRevision})
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "WORKSPACE_IO_FAILED", "Workspace document could not be read or saved", nil)
	default:
		data, _ := json.Marshal(document)
		payload := map[string]any{}
		_ = json.Unmarshal(data, &payload)
		payload["request_id"] = requestID(r.Context())
		writeJSON(w, http.StatusOK, payload)
	}
}

func (s *Server) algorithms(w http.ResponseWriter, r *http.Request) {
	if s.calculations == nil {
		s.notImplemented(w, r)
		return
	}
	definitions, err := s.calculations.Algorithms(r.Context(), requestID(r.Context()), traceID(r.Context()))
	if err != nil {
		s.writeError(w, r, http.StatusServiceUnavailable, "PYTHON_UNAVAILABLE", "Algorithm definitions could not be read", nil)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "algorithms": definitions})
}

func (s *Server) createCalculation(w http.ResponseWriter, r *http.Request) {
	if s.calculations == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request calculation.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_CALCULATION_REQUEST", "Calculation request is invalid", nil)
		return
	}
	result, err := s.calculations.Submit(r.Context(), requestID(r.Context()), traceID(r.Context()), request)
	switch {
	case errors.Is(err, calculation.ErrInvalidRequest):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_CALCULATION_PARAMETERS", "Calculation parameters are invalid", nil)
		return
	case errors.Is(err, calculation.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusServiceUnavailable, "CALCULATION_SUBMIT_FAILED", "Calculation could not be submitted", nil)
		return
	}
	if result.CacheHit {
		writeJSON(w, http.StatusOK, jobPayload(requestID(r.Context()), result.Job))
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "job_id": result.Job.ID, "status": "queued"})
}

func (s *Server) getCalculation(w http.ResponseWriter, r *http.Request) {
	if s.calculations == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.calculations.Job(r.PathValue("job_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "JOB_NOT_FOUND", "Calculation job was not found", nil)
		return
	}
	writeJSON(w, http.StatusOK, jobPayload(requestID(r.Context()), job))
}

func (s *Server) cancelCalculation(w http.ResponseWriter, r *http.Request) {
	if s.calculations == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.calculations.Cancel(r.PathValue("job_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "JOB_NOT_FOUND", "Calculation job was not found", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, jobPayload(requestID(r.Context()), job))
}

func (s *Server) calculationResults(w http.ResponseWriter, r *http.Request) {
	if s.calculations == nil {
		s.notImplemented(w, r)
		return
	}
	from, fromErr := strconv.ParseInt(r.URL.Query().Get("from_bar_index"), 10, 64)
	to, toErr := strconv.ParseInt(r.URL.Query().Get("to_bar_index"), 10, 64)
	if fromErr != nil || toErr != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_RESULT_RANGE", "Result range is invalid", nil)
		return
	}
	result, err := s.calculations.Results(r.PathValue("job_id"), from, to)
	switch {
	case errors.Is(err, calculation.ErrInvalidRange):
		s.writeError(w, r, http.StatusBadRequest, "INVALID_RESULT_RANGE", "Result range is invalid", nil)
		return
	case errors.Is(err, calculation.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "CALCULATION_NOT_READY", "Calculation is not completed", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "JOB_NOT_FOUND", "Calculation job was not found", nil)
		return
	case err != nil:
		s.writeError(w, r, http.StatusInternalServerError, "RESULT_READ_FAILED", "Calculation results could not be read", nil)
		return
	}
	data, _ := json.Marshal(result)
	payload := map[string]any{}
	_ = json.Unmarshal(data, &payload)
	payload["request_id"] = requestID(r.Context())
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) scanDatasets(w http.ResponseWriter, r *http.Request) {
	if s.datasets == nil || s.jobs == nil {
		s.notImplemented(w, r)
		return
	}
	job := s.jobs.Submit("dataset_scan", func(ctx context.Context, progress func(float64)) (string, error) {
		progress(0.05)
		_, err := s.datasets.Scan(ctx)
		if err != nil {
			s.logger.Error("source.scan.failed", "source file scan failed", map[string]any{"reason": err.Error()})
			return "", jobs.Fail("SOURCE_SCAN_FAILED", "Source file scan failed", err)
		}
		progress(1)
		return "source-files", nil
	})
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "job_id": job.ID, "status": "queued"})
}

func (s *Server) sourceFiles(w http.ResponseWriter, r *http.Request) {
	if s.datasets == nil {
		s.notImplemented(w, r)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "items": s.datasets.SourceFiles()})
}

func (s *Server) importDataset(w http.ResponseWriter, r *http.Request) {
	if s.datasets == nil || s.jobs == nil {
		s.notImplemented(w, r)
		return
	}
	defer r.Body.Close()
	var request importer.ImportRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_IMPORT_REQUEST", "Import request is invalid", nil)
		return
	}
	if request.SourceFileID == "" || request.Exchange == "" || request.Instrument == "" || request.Timeframe == "" || request.DateSemantics == "" || request.Timezone == "" {
		s.writeError(w, r, http.StatusUnprocessableEntity, "IMPORT_MAPPING_REQUIRED", "Import mapping fields are required", nil)
		return
	}
	job := s.jobs.Submit("dataset_import", func(ctx context.Context, progress func(float64)) (string, error) {
		s.logger.Info("dataset.import.started", "dataset import started", map[string]any{"source_file_id": request.SourceFileID})
		meta, _, err := s.datasets.Import(ctx, request, progress)
		if err != nil {
			s.logger.Error("dataset.import.failed", "dataset import failed", map[string]any{"reason": err.Error()})
			return "", jobs.Fail("DATASET_IMPORT_FAILED", "Dataset import failed", err)
		}
		for _, file := range meta.Files {
			if file.Role == "bars" {
				return strings.TrimSuffix(file.Path, "bars.parquet") + "meta.json", nil
			}
		}
		return "", nil
	})
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "job_id": job.ID, "status": "queued"})
}

func (s *Server) listDatasets(w http.ResponseWriter, r *http.Request) {
	if s.datasets == nil {
		s.notImplemented(w, r)
		return
	}
	document, metas, err := s.datasets.ListDatasets()
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, "CATALOG_READ_FAILED", "Dataset catalog could not be read", nil)
		return
	}
	items := make([]map[string]any, 0, len(metas))
	for _, meta := range metas {
		items = append(items, map[string]any{
			"dataset_id": meta.DatasetID, "active_revision": meta.DataRevision, "instrument": meta.Instrument.Symbol,
			"timeframe": meta.Timeframe, "bar_count": meta.Coverage.BarCount, "first_timestamp_utc": meta.Coverage.FirstTimestampUTC,
			"last_timestamp_utc": meta.Coverage.LastTimestampUTC, "status": "ready",
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"request_id": requestID(r.Context()), "catalog_revision": document.CatalogRevision, "datasets": items})
}

func (s *Server) getDataset(w http.ResponseWriter, r *http.Request) {
	if s.datasets == nil {
		s.notImplemented(w, r)
		return
	}
	revision := r.URL.Query().Get("revision")
	if revision == "" {
		s.writeError(w, r, http.StatusBadRequest, "REVISION_REQUIRED", "revision is required", nil)
		return
	}
	meta, err := s.datasets.GetDataset(r.PathValue("dataset_id"), revision)
	if errors.Is(err, catalog.ErrNotFound) {
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset revision was not found", nil)
		return
	}
	if err != nil {
		s.writeError(w, r, http.StatusInternalServerError, "DATASET_READ_FAILED", "Dataset metadata could not be read", nil)
		return
	}
	data, _ := json.Marshal(meta)
	payload := map[string]any{}
	_ = json.Unmarshal(data, &payload)
	payload["request_id"] = requestID(r.Context())
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) getBars(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	if s.bars == nil {
		s.notImplemented(w, r)
		return
	}
	values := r.URL.Query()
	revision, generationID := values.Get("revision"), values.Get("generation_id")
	tail, err := optionalPositiveInt(values.Get("tail"))
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_BAR_QUERY", "Bar range query is invalid", nil)
		return
	}
	before, err := optionalPositiveInt64(values.Get("before_bar_index"))
	if err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_BAR_QUERY", "Bar range query is invalid", nil)
		return
	}
	limit := 0
	if raw := values.Get("limit"); raw != "" {
		limit, err = strconv.Atoi(raw)
		if err != nil || limit < 1 {
			s.writeError(w, r, http.StatusBadRequest, "INVALID_BAR_QUERY", "Bar range query is invalid", nil)
			return
		}
	}
	result, err := s.bars.Read(r.Context(), marketdata.Query{
		DatasetID: r.PathValue("dataset_id"), DataRevision: revision, GenerationID: generationID,
		Tail: tail, BeforeBarIndex: before, Limit: limit,
	})
	switch {
	case errors.Is(err, marketdata.ErrInvalidRange):
		s.writeError(w, r, http.StatusBadRequest, "INVALID_BAR_QUERY", "Bar range query is invalid", nil)
		return
	case errors.Is(err, marketdata.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "Dataset revision does not match the active revision", nil)
		return
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "DATASET_NOT_FOUND", "Dataset was not found", nil)
		return
	case err != nil:
		s.logger.Error("bars.read.failed", "K-line range read failed", map[string]any{"reason": err.Error()})
		s.writeError(w, r, http.StatusInternalServerError, "BAR_READ_FAILED", "K-line range could not be read", nil)
		return
	}
	data, _ := json.Marshal(result)
	payload := map[string]any{}
	_ = json.Unmarshal(data, &payload)
	payload["request_id"] = requestID(r.Context())
	s.logger.Info("bars.range.served", "K-line range served", map[string]any{
		"dataset_id": result.DatasetID, "data_revision": result.DataRevision, "generation_id": result.GenerationID,
		"first_bar_index": result.Coverage.FirstBarIndex, "last_bar_index": result.Coverage.LastBarIndex,
		"bar_count": len(result.Bars.BarIndex), "checksum": result.Checksum, "elapsed_ms": time.Since(started).Milliseconds(),
	})
	writeJSON(w, http.StatusOK, payload)
}

func optionalPositiveInt(raw string) (*int, error) {
	if raw == "" {
		return nil, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < 1 {
		return nil, marketdata.ErrInvalidRange
	}
	return &value, nil
}

func optionalPositiveInt64(raw string) (*int64, error) {
	if raw == "" {
		return nil, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value < 1 {
		return nil, marketdata.ErrInvalidRange
	}
	return &value, nil
}

func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	if s.jobs == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.jobs.Get(r.PathValue("job_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "JOB_NOT_FOUND", "Job was not found", nil)
		return
	}
	writeJSON(w, http.StatusOK, jobPayload(requestID(r.Context()), job))
}

func (s *Server) cancelJob(w http.ResponseWriter, r *http.Request) {
	if s.jobs == nil {
		s.notImplemented(w, r)
		return
	}
	job, ok := s.jobs.Cancel(r.PathValue("job_id"))
	if !ok {
		s.writeError(w, r, http.StatusNotFound, "JOB_NOT_FOUND", "Job was not found", nil)
		return
	}
	writeJSON(w, http.StatusAccepted, jobPayload(requestID(r.Context()), job))
}

func jobPayload(requestID string, job *jobs.Job) map[string]any {
	payload := map[string]any{"request_id": requestID, "job_id": job.ID, "status": job.Status, "progress": job.Progress}
	if job.ResultRef != "" {
		payload["result_ref"] = job.ResultRef
	}
	if job.Error != nil {
		payload["error"] = job.Error
	}
	return payload
}

func (s *Server) Handler() http.Handler { return s.handler }

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	pythonHealth := s.python.Health(r.Context())
	status := "ok"
	if pythonHealth.Status != "ok" {
		status = "degraded"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"request_id":       requestID(r.Context()),
		"trace_id":         traceID(r.Context()),
		"status":           status,
		"contract_version": s.contractVersion,
		"services": map[string]any{
			"go-api":        map[string]string{"status": "ok", "version": runtimeVersion},
			"python-engine": map[string]string{"status": pythonHealth.Status, "version": pythonHealth.Version()},
		},
	})
}

func (s *Server) clientLogs(w http.ResponseWriter, r *http.Request) {
	body := http.MaxBytesReader(w, r.Body, s.maxLogBody)
	defer body.Close()
	var payload struct {
		Events []map[string]any `json:"events"`
	}
	decoder := json.NewDecoder(body)
	if err := decoder.Decode(&payload); err != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_LOG_BATCH", "Invalid client log batch", nil)
		return
	}
	if len(payload.Events) < 1 || len(payload.Events) > s.maxLogEvents {
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_LOG_BATCH_SIZE", "Client log batch size is out of range", nil)
		return
	}
	accepted := 0
	for _, event := range payload.Events {
		if validClientEvent(event) {
			s.vueLogger.External(event)
			accepted++
		}
	}
	s.logger.Info("client_logs.batch.received", "client log batch received", map[string]any{"received": len(payload.Events), "accepted": accepted})
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "accepted": accepted})
}

func validClientEvent(event map[string]any) bool {
	required := []string{"timestamp", "level", "event", "message", "source_file", "source_function"}
	for _, key := range required {
		value, ok := event[key].(string)
		if !ok || strings.TrimSpace(value) == "" {
			return false
		}
	}
	line, ok := event["source_line"].(float64)
	return ok && line >= 1
}

func (s *Server) notImplemented(w http.ResponseWriter, r *http.Request) {
	s.writeError(w, r, http.StatusNotImplemented, "ENDPOINT_NOT_IMPLEMENTED", "Endpoint is not implemented in this release", nil)
}

func (s *Server) writeError(w http.ResponseWriter, r *http.Request, status int, code, message string, details map[string]any) {
	writeJSON(w, status, ErrorResponse{Error: APIError{Code: code, Message: message, RequestID: requestID(r.Context()), Details: details}})
}

func (s *Server) requestContext(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		reqID := safeID(r.Header.Get("X-Request-ID"))
		trID := safeID(r.Header.Get("X-Trace-ID"))
		ctx := context.WithValue(r.Context(), requestIDKey, reqID)
		ctx = context.WithValue(ctx, traceIDKey, trID)
		w.Header().Set("X-Request-ID", reqID)
		w.Header().Set("X-Trace-ID", trID)
		s.logger.Info("api.request.started", "API request started", map[string]any{"request_id": reqID, "trace_id": trID, "method": r.Method, "path": r.URL.Path})
		next.ServeHTTP(w, r.WithContext(ctx))
		s.logger.Info("api.request.completed", "API request completed", map[string]any{"request_id": reqID, "trace_id": trID, "duration_ms": float64(time.Since(started).Microseconds()) / 1000})
	})
}

func safeID(value string) string {
	if value != "" && len(value) <= 128 {
		valid := true
		for _, r := range value {
			if !(r == '-' || r == '_' || r >= '0' && r <= '9' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z') {
				valid = false
				break
			}
		}
		if valid {
			return value
		}
	}
	bytes := make([]byte, 12)
	if _, err := io.ReadFull(rand.Reader, bytes); err != nil {
		return time.Now().UTC().Format("20060102T150405.000000000")
	}
	return hex.EncodeToString(bytes)
}

func requestID(ctx context.Context) string {
	value, _ := ctx.Value(requestIDKey).(string)
	return value
}
func traceID(ctx context.Context) string { value, _ := ctx.Value(traceIDKey).(string); return value }

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
