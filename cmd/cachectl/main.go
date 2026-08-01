package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/maintenance"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

func main() {
	if runtime.Version() != "go1.25.7" {
		fmt.Fprintf(os.Stderr, "Go 1.25.7 is required; found %s\n", runtime.Version())
		os.Exit(2)
	}
	configPath := flag.String("config", "config/app.yaml", "path to application configuration")
	kind := flag.String("kind", "all", "cache kind: indicators, chan, replay, or all")
	olderThan := flag.Duration("older-than", 0, "only move entries older than this duration")
	dryRun := flag.Bool("dry-run", true, "print candidates without moving them")
	flag.Parse()
	cfg, err := config.Load(*configPath)
	if err != nil {
		fatal(err)
	}
	guard, err := storage.NewPathGuard(cfg.Storage.DataRoot)
	if err != nil {
		fatal(err)
	}
	moves, err := maintenance.CleanupCaches(guard, maintenance.CleanupOptions{Kind: *kind, OlderThan: *olderThan, DryRun: *dryRun, Now: time.Now().UTC()})
	if err != nil {
		fatal(err)
	}
	result := struct {
		DryRun bool               `json:"dry_run"`
		Count  int                `json:"count"`
		Moves  []maintenance.Move `json:"moves"`
	}{DryRun: *dryRun, Count: len(moves), Moves: moves}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(result)
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
