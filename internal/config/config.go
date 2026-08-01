package config

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

const ContractVersion = "1.0.0"

type Config struct {
	SchemaVersion int `yaml:"schema_version"`
	App           struct {
		Name            string `yaml:"name"`
		Timezone        string `yaml:"timezone"`
		ContractVersion string `yaml:"contract_version"`
	} `yaml:"app"`
	Server struct {
		Listen       string `yaml:"listen"`
		ReadTimeout  string `yaml:"read_timeout"`
		WriteTimeout string `yaml:"write_timeout"`
	} `yaml:"server"`
	PythonEngine struct {
		BaseURL         string `yaml:"base_url"`
		StartupTimeout  string `yaml:"startup_timeout"`
		RequestTimeout  string `yaml:"request_timeout"`
		JobPollInterval string `yaml:"job_poll_interval"`
	} `yaml:"python_engine"`
	Storage struct {
		DataRoot            string `yaml:"data_root"`
		TmpRetentionHours   int    `yaml:"tmp_retention_hours"`
		ParquetRowGroupSize int    `yaml:"parquet_row_group_size"`
	} `yaml:"storage"`
	Import struct {
		SourceDirectory          string `yaml:"source_directory"`
		DefaultEncoding          string `yaml:"default_encoding"`
		FailOnDuplicateTimestamp bool   `yaml:"fail_on_duplicate_timestamp"`
		KeepZeroVolumeBars       bool   `yaml:"keep_zero_volume_bars"`
		FillMissingBars          bool   `yaml:"fill_missing_bars"`
	} `yaml:"import"`
	Chart struct {
		InitialBars               int     `yaml:"initial_bars"`
		PrefetchBars              int     `yaml:"prefetch_bars"`
		MaxBarsPerRequest         int     `yaml:"max_bars_per_request"`
		PrefetchScreenThreshold   float64 `yaml:"prefetch_screen_threshold"`
		ZoomDebounceMS            int     `yaml:"zoom_debounce_ms"`
		OneInflightHistoryRequest bool    `yaml:"one_inflight_history_request"`
	} `yaml:"chart"`
	Workspace struct {
		DefaultProfileID string `yaml:"default_profile_id"`
		DefaultLayoutID  string `yaml:"default_layout_id"`
	} `yaml:"workspace"`
	Logging struct {
		Level              string `yaml:"level"`
		Format             string `yaml:"format"`
		MaxFileBytes       int64  `yaml:"max_file_bytes"`
		BackupCount        int    `yaml:"backup_count"`
		CompressBackups    bool   `yaml:"compress_backups"`
		VueBatchMaxEvents  int    `yaml:"vue_batch_max_events"`
		VueBatchIntervalMS int    `yaml:"vue_batch_interval_ms"`
		VueRequestMaxBytes int64  `yaml:"vue_request_max_bytes"`
	} `yaml:"logging"`
}

func Load(path string) (Config, error) {
	var cfg Config
	f, err := os.Open(path)
	if err != nil {
		return cfg, fmt.Errorf("open config: %w", err)
	}
	defer f.Close()
	decoder := yaml.NewDecoder(f)
	decoder.KnownFields(true)
	if err := decoder.Decode(&cfg); err != nil {
		return cfg, fmt.Errorf("decode config: %w", err)
	}
	if err := cfg.Validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func (c Config) Validate() error {
	if c.SchemaVersion != 1 {
		return fmt.Errorf("unsupported config schema_version %d", c.SchemaVersion)
	}
	if c.App.ContractVersion != ContractVersion {
		return fmt.Errorf("contract_version %q does not match %q", c.App.ContractVersion, ContractVersion)
	}
	if c.Storage.DataRoot == "" {
		return errors.New("storage.data_root is required")
	}
	if c.Storage.TmpRetentionHours < 0 {
		return errors.New("storage.tmp_retention_hours must not be negative")
	}
	if _, _, err := net.SplitHostPort(c.Server.Listen); err != nil {
		return fmt.Errorf("invalid server.listen: %w", err)
	}
	u, err := url.Parse(c.PythonEngine.BaseURL)
	if err != nil || u.Scheme != "http" || u.Hostname() == "" {
		return errors.New("python_engine.base_url must be an http URL")
	}
	if host := u.Hostname(); host != "localhost" {
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			return errors.New("python_engine.base_url must use a loopback host")
		}
	}
	for name, raw := range map[string]string{
		"server.read_timeout":             c.Server.ReadTimeout,
		"server.write_timeout":            c.Server.WriteTimeout,
		"python_engine.startup_timeout":   c.PythonEngine.StartupTimeout,
		"python_engine.request_timeout":   c.PythonEngine.RequestTimeout,
		"python_engine.job_poll_interval": c.PythonEngine.JobPollInterval,
	} {
		if _, err := time.ParseDuration(raw); err != nil {
			return fmt.Errorf("invalid %s: %w", name, err)
		}
	}
	if c.Logging.Format != "ndjson" || c.Logging.MaxFileBytes <= 0 || c.Logging.BackupCount != 9 {
		return errors.New("logging must use ndjson, a positive max_file_bytes, and backup_count 9")
	}
	if c.Logging.VueBatchMaxEvents < 1 || c.Logging.VueBatchMaxEvents > 100 {
		return errors.New("logging.vue_batch_max_events must be between 1 and 100")
	}
	if c.Chart.InitialBars < 1 || c.Chart.PrefetchBars < 1 || c.Chart.MaxBarsPerRequest < c.Chart.InitialBars || c.Chart.MaxBarsPerRequest < c.Chart.PrefetchBars {
		return errors.New("chart bar counts must be positive and no larger than max_bars_per_request")
	}
	if c.Chart.PrefetchScreenThreshold <= 0 || c.Chart.ZoomDebounceMS < 0 {
		return errors.New("chart prefetch threshold must be positive and zoom debounce must be non-negative")
	}
	return nil
}

func (c Config) ReadTimeout() time.Duration {
	d, _ := time.ParseDuration(c.Server.ReadTimeout)
	return d
}

func (c Config) WriteTimeout() time.Duration {
	d, _ := time.ParseDuration(c.Server.WriteTimeout)
	return d
}

func (c Config) PythonRequestTimeout() time.Duration {
	d, _ := time.ParseDuration(c.PythonEngine.RequestTimeout)
	return d
}

func (c Config) PythonJobPollInterval() time.Duration {
	d, _ := time.ParseDuration(c.PythonEngine.JobPollInterval)
	return d
}
