package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/api"
	"github.com/tvbt/tradingview-historical-backtest/internal/backtest"
	"github.com/tvbt/tradingview-historical-backtest/internal/calculation"
	"github.com/tvbt/tradingview-historical-backtest/internal/catalog"
	"github.com/tvbt/tradingview-historical-backtest/internal/comparison"
	"github.com/tvbt/tradingview-historical-backtest/internal/config"
	"github.com/tvbt/tradingview-historical-backtest/internal/importer"
	"github.com/tvbt/tradingview-historical-backtest/internal/jobs"
	"github.com/tvbt/tradingview-historical-backtest/internal/logx"
	"github.com/tvbt/tradingview-historical-backtest/internal/maintenance"
	"github.com/tvbt/tradingview-historical-backtest/internal/marketdata"
	"github.com/tvbt/tradingview-historical-backtest/internal/optimization"
	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/replay"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
	"github.com/tvbt/tradingview-historical-backtest/internal/workspace"
)

func main() {
	if runtime.Version() != "go1.25.7" {
		fmt.Fprintf(os.Stderr, "Go 1.25.7 is required; found %s\n", runtime.Version())
		os.Exit(2)
	}
	configPath := flag.String("config", "config/app.yaml", "path to application configuration")
	flag.Parse()
	cfg, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "configuration error: %v\n", err)
		os.Exit(2)
	}
	guard, err := storage.NewPathGuard(cfg.Storage.DataRoot)
	if err != nil {
		fmt.Fprintf(os.Stderr, "data_root error: %v\n", err)
		os.Exit(2)
	}
	goLogPath, _ := guard.Resolve("logs/go/app.log")
	vueLogPath, _ := guard.Resolve("logs/vue/client.log")
	projectRoot, _ := filepath.Abs(".")
	maxSizeMB := int(cfg.Logging.MaxFileBytes / (1024 * 1024))
	logger, err := logx.New(logx.Options{Service: "go-api", Path: goLogPath, MaxSizeMB: maxSizeMB, MaxBackups: cfg.Logging.BackupCount, Compress: cfg.Logging.CompressBackups, ConsoleWriter: os.Stdout, ProjectRoot: projectRoot})
	if err != nil {
		fmt.Fprintf(os.Stderr, "log setup degraded: %v\n", err)
		logger, _ = logx.New(logx.Options{Service: "go-api", Writer: ioDiscard{}, ConsoleWriter: os.Stdout})
	}
	defer logger.Close()
	vueLogger, err := logx.New(logx.Options{Service: "vue-client", Path: vueLogPath, MaxSizeMB: maxSizeMB, MaxBackups: cfg.Logging.BackupCount, Compress: cfg.Logging.CompressBackups, ConsoleWriter: os.Stdout, ProjectRoot: projectRoot})
	if err != nil {
		logger.Warn("logging.degraded", "Vue log file is unavailable", map[string]any{"reason": err.Error()})
		vueLogger, _ = logx.New(logx.Options{Service: "vue-client", Writer: ioDiscard{}, ConsoleWriter: os.Stdout})
	}
	defer vueLogger.Close()
	recovered, err := maintenance.RecoverStaleTemps(guard, time.Duration(cfg.Storage.TmpRetentionHours)*time.Hour, time.Now().UTC())
	if err != nil {
		logger.Error("storage.recovery.failed", "Stale temporary directories could not be recovered", map[string]any{"reason": err.Error()})
		fmt.Fprintln(os.Stderr, "temporary recovery error")
		os.Exit(1)
	}
	if len(recovered) > 0 {
		logger.Warn("storage.recovery.completed", "Stale temporary directories were moved to trash", map[string]any{"count": len(recovered)})
	}
	catalogStore, err := catalog.NewStore(guard)
	if err != nil {
		logger.Error("catalog.load.failed", "Catalog could not be loaded", map[string]any{"reason": err.Error()})
		fmt.Fprintln(os.Stderr, "catalog error")
		os.Exit(1)
	}
	datasetService := importer.NewService(guard, catalogStore, cfg, logger)
	barReader := marketdata.NewReader(guard, catalogStore, marketdata.Config{
		InitialBars: cfg.Chart.InitialBars, PrefetchBars: cfg.Chart.PrefetchBars,
		MaxBarsPerRequest: cfg.Chart.MaxBarsPerRequest, MaxCachedDatasets: 8,
	})
	jobManager, err := jobs.NewPersistentManager(guard)
	if err != nil {
		logger.Error("jobs.restore.failed", "Persistent job state could not be restored", map[string]any{"reason": err.Error()})
		fmt.Fprintln(os.Stderr, "job store error")
		os.Exit(1)
	}
	python := pythonclient.New(cfg.PythonEngine.BaseURL, cfg.App.ContractVersion, cfg.PythonRequestTimeout())
	calculationService := calculation.NewService(guard, catalogStore, python, jobManager, cfg.App.ContractVersion, cfg.PythonJobPollInterval())
	replayService := replay.NewService(guard, catalogStore, python, jobManager, cfg.App.ContractVersion, cfg.PythonJobPollInterval())
	backtestService := backtest.NewService(guard, catalogStore, python, jobManager, cfg.App.ContractVersion, cfg.PythonJobPollInterval())
	optimizationService := optimization.NewService(guard, catalogStore, python, jobManager, cfg.App.ContractVersion, cfg.PythonJobPollInterval())
	comparisonService := comparison.NewService(guard, catalogStore, python, jobManager, cfg.App.ContractVersion, cfg.PythonJobPollInterval())
	workspaceStore := workspace.NewStore(guard)
	server := &http.Server{Addr: cfg.Server.Listen, Handler: api.NewServer(cfg, python, logger, vueLogger, api.WithDatasets(datasetService, jobManager), api.WithBarReader(barReader), api.WithCalculations(calculationService), api.WithReplays(replayService), api.WithBacktests(backtestService), api.WithOptimization(optimizationService), api.WithComparisons(comparisonService), api.WithWorkspace(workspaceStore)).Handler(), ReadTimeout: cfg.ReadTimeout(), WriteTimeout: cfg.WriteTimeout()}
	logger.Info("app.started", "Go API starting", map[string]any{"listen": cfg.Server.Listen, "contract_version": cfg.App.ContractVersion})
	errCh := make(chan error, 1)
	go func() { errCh <- server.ListenAndServe() }()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	select {
	case sig := <-stop:
		logger.Info("app.stopped", "Go API stopping", map[string]any{"signal": sig.String()})
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
	case err := <-errCh:
		if err != nil && err != http.ErrServerClosed {
			logger.Error("app.failed", "Go API stopped unexpectedly", map[string]any{"reason": err.Error()})
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
}

type ioDiscard struct{}

func (ioDiscard) Write(p []byte) (int, error) { return len(p), nil }
