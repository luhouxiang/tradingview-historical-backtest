package logx

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
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
	_, file, line, ok := runtime.Caller(skip)
	if ok {
		file = l.trimPath(file)
	} else {
		file, line = "unknown", 1
	}
	l.writeText(time.Now(), level, file, line, logMessage(event, message, fields))
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
	level, _ := record["level"].(string)
	if level == "" {
		level = "INFO"
	}
	file, _ := record["source_file"].(string)
	if file == "" {
		file = "unknown"
	}
	line := numberField(record["source_line"], 1)
	event, _ := record["event"].(string)
	message, _ := record["message"].(string)
	fields := externalFields(record)
	l.writeText(time.Now(), level, file, line, logMessage(event, message, fields))
}

func (l *Logger) writeText(timestamp time.Time, level, file string, line int, message string) {
	prefix := fmt.Sprintf("[%s][%s][%s][%03d] ", timestamp.Format("2006-01-02 15:04:05.000"), level, file, line)
	lines := strings.Split(strings.ReplaceAll(message, "\r\n", "\n"), "\n")
	var builder strings.Builder
	for _, value := range lines {
		builder.WriteString(prefix)
		builder.WriteString(value)
		builder.WriteByte('\n')
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = l.writer.Write([]byte(builder.String()))
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

func logMessage(event, message string, fields map[string]any) string {
	parts := make([]string, 0, 3)
	if event != "" {
		parts = append(parts, event)
	}
	if message != "" {
		parts = append(parts, message)
	}
	if len(fields) > 0 {
		if data, err := json.Marshal(orderedMap(fields)); err == nil {
			parts = append(parts, string(data))
		}
	}
	return strings.Join(parts, " ")
}

func orderedMap(fields map[string]any) map[string]any {
	ordered := make(map[string]any, len(fields))
	keys := make([]string, 0, len(fields))
	for key := range fields {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		ordered[key] = fields[key]
	}
	return ordered
}

func numberField(value any, fallback int) int {
	switch typed := value.(type) {
	case int:
		return typed
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	default:
		return fallback
	}
}

func externalFields(record map[string]any) map[string]any {
	fields := make(map[string]any)
	for key, value := range record {
		switch key {
		case "timestamp", "level", "service", "event", "message", "source_file", "source_line", "source_function":
			continue
		default:
			fields[key] = value
		}
	}
	return fields
}
