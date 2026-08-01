package logx

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"gopkg.in/natefinch/lumberjack.v2"
)

type Options struct {
	Service     string
	Path        string
	MaxSizeMB   int
	MaxBackups  int
	Compress    bool
	Writer      io.Writer
	ProjectRoot string
}

type Logger struct {
	mu          sync.Mutex
	service     string
	writer      io.Writer
	closer      io.Closer
	projectRoot string
}

func New(options Options) (*Logger, error) {
	writer := options.Writer
	var closer io.Closer
	if writer == nil {
		if err := os.MkdirAll(filepath.Dir(options.Path), 0o750); err != nil {
			return nil, err
		}
		rotator := &lumberjack.Logger{
			Filename:   options.Path,
			MaxSize:    options.MaxSizeMB,
			MaxBackups: options.MaxBackups,
			Compress:   options.Compress,
			LocalTime:  true,
		}
		writer = rotator
		closer = rotator
	}
	root, _ := filepath.Abs(options.ProjectRoot)
	return &Logger{service: options.Service, writer: writer, closer: closer, projectRoot: root}, nil
}

func (l *Logger) Debug(event, message string, fields map[string]any) {
	l.log("DEBUG", event, message, fields, 2)
}
func (l *Logger) Info(event, message string, fields map[string]any) {
	l.log("INFO", event, message, fields, 2)
}
func (l *Logger) Warn(event, message string, fields map[string]any) {
	l.log("WARN", event, message, fields, 2)
}
func (l *Logger) Error(event, message string, fields map[string]any) {
	l.log("ERROR", event, message, fields, 2)
}

func (l *Logger) log(level, event, message string, fields map[string]any, skip int) {
	pc, file, line, ok := runtime.Caller(skip)
	function := "unknown"
	if ok {
		if fn := runtime.FuncForPC(pc); fn != nil {
			function = fn.Name()
		}
	} else {
		file, line = "unknown", 1
	}
	record := map[string]any{
		"timestamp":       time.Now().Format(time.RFC3339Nano),
		"level":           level,
		"service":         l.service,
		"event":           event,
		"message":         message,
		"source_file":     l.trimPath(file),
		"source_line":     line,
		"source_function": function,
	}
	extra := make(map[string]any)
	for key, value := range fields {
		if isContextField(key) {
			record[key] = value
		} else {
			extra[key] = value
		}
	}
	if len(extra) > 0 {
		record["fields"] = extra
	}
	l.write(record)
}

func isContextField(key string) bool {
	switch key {
	case "request_id", "trace_id", "job_id", "run_id", "replay_id", "dataset_id", "data_revision",
		"algorithm_id", "algorithm_version", "strategy_id", "strategy_version", "cache_key", "bar_index",
		"bar_time", "sequence", "stage_signal_id", "signal_id", "parent_signal_id", "duration_ms":
		return true
	default:
		return false
	}
}

func (l *Logger) External(record map[string]any) {
	clone := make(map[string]any, len(record)+2)
	for key, value := range record {
		clone[key] = value
	}
	clone["service"] = l.service
	clone["received_at"] = time.Now().Format(time.RFC3339Nano)
	l.write(clone)
}

func (l *Logger) write(record map[string]any) {
	data, err := json.Marshal(record)
	if err != nil {
		return
	}
	data = append(data, '\n')
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = l.writer.Write(data)
}

func (l *Logger) trimPath(path string) string {
	abs, err := filepath.Abs(path)
	if err == nil && l.projectRoot != "" {
		if rel, relErr := filepath.Rel(l.projectRoot, abs); relErr == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return filepath.ToSlash(rel)
		}
	}
	return filepath.Base(path)
}

func (l *Logger) Close() error {
	if l.closer != nil {
		return l.closer.Close()
	}
	return nil
}
