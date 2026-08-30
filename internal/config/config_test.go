package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoadExample(t *testing.T) {
	cfg, err := Load(filepath.Join("..", "..", "config", "app.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.App.ContractVersion != ContractVersion || cfg.Logging.BackupCount != 9 {
		t.Fatalf("unexpected config: %#v", cfg)
	}
	if cfg.InitialInstrument() != "AOL9" {
		t.Fatalf("unexpected initial instrument %q", cfg.InitialInstrument())
	}
	if cfg.Chart.BeginDT != "2026-04-27 11:30:00" || cfg.Chart.EndDT != "2026-07-24 21:50:00" {
		t.Fatalf("unexpected chart time bounds %q/%q", cfg.Chart.BeginDT, cfg.Chart.EndDT)
	}
}

func TestLoadRejectsUnknownField(t *testing.T) {
	path := filepath.Join(t.TempDir(), "app.yaml")
	data, err := os.ReadFile(filepath.Join("..", "..", "config", "app.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	data = append(data, []byte("\nunknown: true\n")...)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("expected unknown field to be rejected")
	}
}

func TestLoadDefaultsInitialInstrument(t *testing.T) {
	path := filepath.Join(t.TempDir(), "app.yaml")
	data, err := os.ReadFile(filepath.Join("..", "..", "config", "app.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Replace(string(data), "  initial_instrument: AOL9\r\n", "", 1)
	text = strings.Replace(text, "  initial_instrument: AOL9\n", "", 1)
	data = []byte(text)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InitialInstrument() != "AOL9" {
		t.Fatalf("unexpected default initial instrument %q", cfg.InitialInstrument())
	}
}

func TestChartTimeBoundsUTCParsesValidBoundsAndIgnoresInvalidValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), "app.yaml")
	data, err := os.ReadFile(filepath.Join("..", "..", "config", "app.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Replace(string(data), `begin_dt: "2026-04-27 11:30:00"`, `begin_dt: "2026-08-10 14:10:00"`, 1)
	text = strings.Replace(text, `end_dt: "2026-07-24 21:50:00"`, `end_dt: "bad-time"`, 1)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	begin, end := cfg.ChartTimeBoundsUTC()
	location, _ := time.LoadLocation("Asia/Shanghai")
	want := time.Date(2026, 8, 10, 14, 10, 0, 0, location).UTC().UnixMilli()
	if begin == nil || *begin != want {
		t.Fatalf("begin bound = %v, want %d", begin, want)
	}
	if end != nil {
		t.Fatalf("invalid end bound should be ignored: %v", *end)
	}
}
