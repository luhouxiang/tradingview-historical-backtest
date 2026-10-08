package api

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/training"
)

func (s *Server) createTraining(w http.ResponseWriter, r *http.Request) {
	if s.training == nil {
		s.notImplemented(w, r)
		return
	}
	var input training.CreateRequest
	if !s.decodeTraining(w, r, &input) {
		return
	}
	result, err := s.training.Create(r.Context(), requestID(r.Context()), traceID(r.Context()), input)
	if s.trainingError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "session_id": result.SessionID, "job_id": result.Job.ID, "status": result.Job.Status})
}
func (s *Server) commandTraining(w http.ResponseWriter, r *http.Request) {
	if s.training == nil {
		s.notImplemented(w, r)
		return
	}
	var command training.Command
	if !s.decodeTraining(w, r, &command) {
		return
	}
	if command.SessionID != r.PathValue("session_id") {
		s.trainingError(w, r, training.ErrInvalid)
		return
	}
	result, err := s.training.Command(r.Context(), requestID(r.Context()), traceID(r.Context()), command)
	if s.trainingError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"request_id": requestID(r.Context()), "session_id": result.SessionID, "job_id": result.Job.ID, "status": result.Job.Status})
}
func (s *Server) getTraining(w http.ResponseWriter, r *http.Request) {
	if s.training == nil {
		s.notImplemented(w, r)
		return
	}
	job, snapshot, ok := s.training.Status(r.PathValue("session_id"))
	if !ok {
		s.trainingError(w, r, catalog.ErrNotFound)
		return
	}
	result := map[string]any{"request_id": requestID(r.Context()), "session_id": r.PathValue("session_id"), "job_id": job.ID, "status": job.Status, "snapshot": snapshot}
	if job.Error != nil {
		result["error"] = job.Error
	}
	writeJSON(w, http.StatusOK, result)
}
func (s *Server) decodeTraining(w http.ResponseWriter, r *http.Request, value any) bool {
	defer r.Body.Close()
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024*1024))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	if decoder.Decode(value) != nil {
		s.writeError(w, r, http.StatusBadRequest, "INVALID_TRAINING_REQUEST", "训练请求格式错误", nil)
		return false
	}
	return true
}
func (s *Server) trainingError(w http.ResponseWriter, r *http.Request, err error) bool {
	if err == nil {
		return false
	}
	switch {
	case errors.Is(err, catalog.ErrNotFound):
		s.writeError(w, r, http.StatusNotFound, "TRAINING_NOT_FOUND", "训练会话或数据不存在；服务重启后请重新开始训练", nil)
	case errors.Is(err, backtest.ErrRevisionMismatch):
		s.writeError(w, r, http.StatusConflict, "DATA_REVISION_MISMATCH", "数据修订已变更，请重新开始训练", nil)
	case errors.Is(err, training.ErrConflict):
		s.writeError(w, r, http.StatusConflict, "TRAINING_CONFLICT", "训练游标或固定事实冲突，请刷新会话状态", nil)
	case errors.Is(err, training.ErrNotReady):
		s.writeError(w, r, http.StatusConflict, "TRAINING_BUSY", "前一条训练命令尚未完成", nil)
	case errors.Is(err, training.ErrInvalid), errors.Is(err, backtest.ErrInvalidRequest), errors.Is(err, backtest.ErrInvalidRange):
		s.writeError(w, r, http.StatusUnprocessableEntity, "INVALID_TRAINING_PARAMETERS", "训练参数或控制范围无效", nil)
	default:
		s.writeError(w, r, http.StatusServiceUnavailable, "TRAINING_SUBMIT_FAILED", "训练命令暂时无法提交", nil)
	}
	return true
}
