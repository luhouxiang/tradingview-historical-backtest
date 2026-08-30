package catalog

import "time"

type DatasetMeta struct {
	SchemaVersion     int            `json:"schema_version"`
	DatasetID         string         `json:"dataset_id"`
	DataRevision      string         `json:"data_revision"`
	IndependenceGroup string         `json:"independence_group,omitempty"`
	Instrument        InstrumentMeta `json:"instrument"`
	Timeframe         string         `json:"timeframe"`
	Source            SourceMeta     `json:"source"`
	Time              TimeMeta       `json:"time"`
	Price             PriceMeta      `json:"price"`
	Coverage          CoverageMeta   `json:"coverage"`
	Importer          ImporterMeta   `json:"importer"`
	Quality           QualityCounts  `json:"quality"`
	Files             []FileMeta     `json:"files"`
	CreatedAt         time.Time      `json:"created_at"`
}

type InstrumentMeta struct {
	Exchange           string `json:"exchange"`
	Symbol             string `json:"symbol"`
	Product            string `json:"product"`
	DisplayName        string `json:"display_name,omitempty"`
	ContractMultiplier int64  `json:"contract_multiplier,omitempty"`
}

type SourceMeta struct {
	Path               string `json:"path"`
	SHA256             string `json:"sha256"`
	Encoding           string `json:"encoding"`
	Format             string `json:"format"`
	Title              string `json:"title,omitempty"`
	TimestampSemantics string `json:"timestamp_semantics,omitempty"`
}

type TimeMeta struct {
	Timezone            string `json:"timezone"`
	DateSemantics       string `json:"date_semantics"`
	TradingCalendarHash string `json:"trading_calendar_hash"`
	SessionConfigHash   string `json:"session_config_hash,omitempty"`
}

type PriceMeta struct {
	PriceDecimals int   `json:"price_decimals"`
	PriceScale    int64 `json:"price_scale"`
	TickSizeI64   int64 `json:"tick_size_i64,omitempty"`
}

type CoverageMeta struct {
	BarCount          int64  `json:"bar_count"`
	FirstBarIndex     int64  `json:"first_bar_index"`
	LastBarIndex      int64  `json:"last_bar_index"`
	FirstTimestampUTC int64  `json:"first_timestamp_utc"`
	LastTimestampUTC  int64  `json:"last_timestamp_utc"`
	FirstTradingDay   string `json:"first_trading_day"`
	LastTradingDay    string `json:"last_trading_day"`
	TradingDayCount   int    `json:"trading_day_count,omitempty"`
}

type ImporterMeta struct {
	ID          string `json:"id"`
	Version     string `json:"version"`
	OptionsHash string `json:"options_hash"`
}

type QualityCounts struct {
	DuplicateCount   int `json:"duplicate_count"`
	InvalidOHLCCount int `json:"invalid_ohlc_count"`
	ZeroVolumeCount  int `json:"zero_volume_count"`
	GapCount         int `json:"gap_count"`
	WarningCount     int `json:"warning_count"`
	ErrorCount       int `json:"error_count"`
}

type FileMeta struct {
	Role      string `json:"role"`
	Path      string `json:"path"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
}

type Document struct {
	SchemaVersion   int            `json:"schema_version"`
	CatalogRevision int64          `json:"catalog_revision"`
	UpdatedAt       time.Time      `json:"updated_at"`
	Datasets        []DatasetEntry `json:"datasets"`
}

type DatasetEntry struct {
	DatasetID      string `json:"dataset_id"`
	ActiveRevision string `json:"active_revision"`
	MetaPath       string `json:"meta_path"`
	Status         string `json:"status"`
}
