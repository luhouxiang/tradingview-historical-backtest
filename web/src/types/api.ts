export type ServiceStatus = 'ok' | 'degraded' | 'unavailable'

export interface HealthResponse {
  request_id: string
  trace_id?: string
  status: ServiceStatus
  contract_version: string
  services: Record<string, { status: ServiceStatus; version?: string }>
}

export interface ErrorResponse {
  error: {
    code: string
    message: string
    request_id: string
    details?: Record<string, unknown>
  }
}

export interface JobAccepted {
  request_id: string
  job_id: string
  status: 'queued'
}

export interface JobStatus {
  request_id: string
  job_id: string
  status: 'queued' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  progress: number
  result_ref?: string
  error?: { code: string; message: string }
}

export interface SourceFile {
  source_file_id: string
  path: string
  status: 'detected' | 'needs_mapping' | 'importable' | 'imported' | 'rejected'
  sha256: string
  size_bytes: number
  detected?: Record<string, unknown>
  issues: Array<{ level: string; code: string; message: string; source_line?: number }>
}

export interface DatasetSummary {
  dataset_id: string
  active_revision: string
  instrument: string
  timeframe: string
  bar_count: number
  first_timestamp_utc: number
  last_timestamp_utc: number
  status: 'ready' | 'importing' | 'invalid'
}

export interface DatasetMeta {
  request_id: string
  dataset_id: string
  data_revision: string
  instrument: { exchange: string; symbol: string; product: string; display_name?: string }
  timeframe: string
  source: { path: string; encoding: string; format: string; title?: string; timestamp_semantics?: 'bar_start' | 'bar_end' }
  time: { timezone: string; date_semantics: string }
  price: { price_decimals: number; price_scale: number; tick_size_i64?: number }
  coverage: {
    bar_count: number
    first_bar_index: number
    last_bar_index: number
    first_timestamp_utc: number
    last_timestamp_utc: number
    first_trading_day: string
    last_trading_day: string
  }
  quality: Record<string, number>
}

export interface BarColumns {
  bar_index: number[]
  timestamp_utc: number[]
  open_i64: number[]
  high_i64: number[]
  low_i64: number[]
  close_i64: number[]
  volume: number[]
  open_interest: Array<number | null>
}

export interface BarRangeResponse {
  request_id: string
  dataset_id: string
  data_revision: string
  generation_id: string
  price_scale: number
  coverage: { first_bar_index: number; last_bar_index: number }
  has_more_before: boolean
  checksum: string
  bars: BarColumns
}

export interface AlgorithmRef {
  kind: 'indicator' | 'chan' | 'strategy'
  algorithm_id: string
  algorithm_version: string
  source_hash: string
}

export interface AlgorithmOutput {
  name: string
  display_name: string
  pane: 'main' | 'indicator'
  series_type: 'line' | 'histogram' | 'semantic_objects'
  object_type?: 'fractal' | 'bi' | 'segment' | 'zhongshu' | 'strategy_state' | 'stage_signal' | 'trade_signal' | 'chart_event'
}

export interface AlgorithmDefinition extends AlgorithmRef {
  name: string
  input_schema: 'bars.v1'
  parameter_schema: {
    type: 'object'
    properties: Record<string, {
      type: 'integer' | 'number' | 'string' | 'boolean'
      minimum?: number
      maximum?: number
      enum?: string[]
      default?: string | number | boolean
    }>
    required: string[]
    additionalProperties: false
  }
  outputs: AlgorithmOutput[]
  warmup: { kind: 'formula'; expression: string }
  causal: true
}

export interface CalculationRequest {
  dataset_id: string
  data_revision: string
  algorithm: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  calculation_mode: 'full_history' | 'causal_events'
}

interface CalculationResultBase {
  request_id: string
  job_id: string
  cache_key: string
  dataset_id: string
  data_revision: string
  algorithm: AlgorithmRef
  coverage: { first_bar_index: number; last_bar_index: number; returned_count: number }
  checksum: string
}

export interface IndicatorCalculationResults extends CalculationResultBase {
  result_kind: 'indicator'
  bar_index: number[]
  values: Record<string, Array<number | null>>
}

export interface ChanFractal {
  object_id: string
  bar_index: number
  time: number
  price_i64: number
  fractal_type: 'top' | 'bottom'
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanLineObject {
  object_id: string
  start_bar_index: number
  start_time: number
  start_price_i64: number
  end_bar_index: number
  end_time: number
  end_price_i64: number
  direction: 'up' | 'down'
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanZhongshu {
  object_id: string
  start_bar_index: number
  start_time: number
  end_bar_index: number
  end_time: number
  zg_i64: number
  zd_i64: number
  confirmed: boolean
  confirmed_at_bar_index: number | null
  status: 'confirmed' | 'extended' | 'left'
  leave_direction: 'up' | 'down' | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanCalculationResults extends CalculationResultBase {
  result_kind: 'chan'
  objects: {
    fractals: ChanFractal[]
    bi: ChanLineObject[]
    segments: ChanLineObject[]
    zhongshu: ChanZhongshu[]
  }
}

export type CalculationResults = IndicatorCalculationResults | ChanCalculationResults

export interface ReplayRequest {
  dataset_id: string
  data_revision: string
  strategy: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  from_bar_index: number
  to_bar_index: number
  warmup_from_bar_index: number
}

export interface ReplayStatus {
  request_id: string
  replay_id: string
  status: JobStatus['status']
  progress: number
  cache_key?: string
  result_ref?: string
  error?: { code: string; message: string }
}

export interface CausalEvent {
  event_seq: number
  known_at_bar_index: number
  object_type: 'fractal' | 'bi' | 'segment' | 'zhongshu' | 'strategy_state' | 'stage_signal' | 'trade_signal' | 'chart_event'
  object_id: string
  operation: 'upsert' | 'delete'
  object_revision: number
  payload: Record<string, unknown>
}

export interface ReplayEventsResponse {
  request_id: string
  replay_id: string
  known_from_bar_index: number
  known_to_bar_index: number
  event_count: number
  checksum: string
  events: CausalEvent[]
}

export interface BacktestRequest {
  dataset_id: string
  data_revision: string
  strategy: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  range: { warmup_from_bar_index: number; from_bar_index: number; to_bar_index: number }
  execution: {
    signal_timing: 'bar_close'
    fill_timing: 'next_bar_open' | 'bar_close'
    commission: Record<string, string | number>
    slippage: { mode: 'ticks' | 'bps'; value: number }
    contract_multiplier: number
    margin_ratio: number
    intrabar_conflict_rule: 'stop_first' | 'target_first' | 'worst_case'
  }
  capital: { initial_cash_i64: number; currency: string; money_scale: number }
  random_seed: number
  trace_id?: string
}

export interface RunAccepted {
  request_id: string
  run_id: string
  run_signature: string
  status: 'queued'
}

export interface RunStatus {
  request_id: string
  run_id: string
  run_signature: string
  status: JobStatus['status']
  progress: number
  manifest?: Record<string, unknown>
  error?: { code: string; message: string }
}

export interface BacktestSummary {
  request_id: string
  run_id: string
  total_return: number
  annualized_return: number | null
  max_drawdown: number
  sharpe: number | null
  trade_count: number
  win_rate: number | null
  average_win_i64: number | null
  average_loss_i64: number | null
  profit_loss_ratio: number | null
  profit_factor: number | null
  expectancy_i64: number | null
  total_commission_i64: number
  total_slippage_i64: number
}

export interface BacktestTrade {
  trade_id: string
  side: 'long' | 'short'
  entry_bar_index: number
  entry_time: number
  entry_price_i64: number
  exit_bar_index: number
  exit_time: number
  exit_price_i64: number
  quantity: number
  gross_pnl_i64: number
  net_pnl_i64: number
  commission_i64: number
  slippage_i64: number
}

export interface EquityRow {
  bar_index: number
  timestamp_utc: number
  equity_i64: number
  cash_i64: number
  available_i64: number
  margin_i64: number
  drawdown: number
}

export type StudyMetric = 'total_return' | 'sharpe' | 'max_drawdown' | 'win_rate' | 'trade_count' | 'profit_factor' | 'expectancy_i64'

export interface StudyRequest {
  dataset_id: string
  data_revision: string
  strategy: AlgorithmRef
  base_parameters: Record<string, string | number | boolean>
  search_space: Array<{
    name: string
    type: 'integer' | 'number' | 'boolean' | 'string'
    minimum?: number
    maximum?: number
    step?: number
    candidates?: Array<string | number | boolean>
  }>
  objectives: Array<{ metric: StudyMetric; direction: 'maximize' | 'minimize' }>
  constraints: Array<{ metric: StudyMetric; operator: '>=' | '<='; value: number }>
  search: { method: 'grid' | 'random'; budget: number; random_seed: number }
  ranges: {
    train: { warmup_from_bar_index: number; from_bar_index: number; to_bar_index: number }
    validation: { warmup_from_bar_index: number; from_bar_index: number; to_bar_index: number }
  }
  execution: BacktestRequest['execution']
  capital: BacktestRequest['capital']
}

export interface StudyAccepted {
  request_id: string
  study_id: string
  status: 'queued'
}

export interface StudyStatus {
  request_id: string
  study_id: string
  status: JobStatus['status']
  progress: number
  result_ref?: string
  manifest?: Record<string, unknown>
  error?: { code: string; message: string }
}

export interface StudyEvaluation {
  evaluation_index: number
  parameters: Record<string, string | number | boolean>
  constraints_satisfied: boolean
  status: 'completed'
  train_run_id: string
  train_run_signature: string
  validation_run_id: string
  validation_run_signature: string
  train_metrics: Partial<Record<StudyMetric, number | null>>
  validation_metrics: Partial<Record<StudyMetric, number | null>>
  train_rank: number
  validation_rank: number
}

export interface StudyEvaluations {
  request_id: string
  study_id: string
  evaluations: StudyEvaluation[]
  stability: {
    selected_evaluation_index: number
    selected_train_rank: number
    selected_validation_rank: number
    primary_metric: StudyMetric
    train_primary_value: number | null
    validation_primary_value: number | null
    primary_absolute_gap: number | null
    constraint_feasible_count: number
    top_train_evaluation_indices: number[]
    top_train_validation_rank_mean: number
    train_validation_rank_correlation: number | null
    stable_selection: boolean
    warnings: string[]
  }
}

export interface SeriesSource {
  source_type: 'SeriesSource'
  source_id: string
  definition: AlgorithmDefinition
  parameters: Record<string, string | number | boolean>
  job_id: string
  status: JobStatus['status']
  style?: IndicatorStyle
  error?: string
}

export interface StrategySource {
  source_type: 'StrategySource'
  source_id: string
  definition: AlgorithmDefinition
  parameters: Record<string, string | number | boolean>
  job_id: string
  status: JobStatus['status']
  visible: boolean
  category_visibility: { fractals: boolean; bi: boolean; segments: boolean; zhongshu: boolean }
  style?: IndicatorStyle
  error?: string
}

export type IndicatorLineStyleName = 'solid' | 'dashed' | 'dotted'

export interface IndicatorOutputStyle {
  color: string
  line_width: 1 | 2 | 3 | 4
  line_style: IndicatorLineStyleName
  opacity: number
  visible: boolean
}

export interface IndicatorStyle {
  outputs: Record<string, IndicatorOutputStyle>
}

export interface WorkspacePane {
  id: string
  role: 'price' | 'indicator'
  weight: number
  min_height: number
  visible: boolean
  collapsed: boolean
  order: number
  title?: string
}

export interface PersistedSeriesSource {
  source_id: string
  name: string
  pane_id: string
  visible: boolean
  locked: boolean
  z_band: 400
  order_in_band: number
  dataset_id: string
  data_revision: string
  algorithm: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  style?: IndicatorStyle
}

export interface PersistedStrategySource {
  source_id: string
  name: string
  pane_id: string
  visible: boolean
  locked: boolean
  z_band: 500
  order_in_band: number
  dataset_id: string
  data_revision: string
  algorithm: AlgorithmRef & { kind: 'chan' }
  parameters: Record<string, string | number | boolean>
  category_visibility: { fractals: boolean; bi: boolean; segments?: boolean; zhongshu: boolean }
  style?: IndicatorStyle
}

export interface WorkspaceLayout {
  request_id?: string
  schema_version: 1
  layout_id: string
  profile_id: string
  revision: number
  panes: WorkspacePane[]
  right_panel: { width: number; collapsed: boolean; active_tab: 'watchlist' | 'object_tree' | 'data_window' | 'strategy_params' }
  bottom_panel: { height: number; collapsed: boolean; active_tab: 'replay' | 'backtest' | 'trades' | 'equity' | 'optimization' | 'tasks' | 'logs' }
  object_order: Array<{ id: string; pane_id: string; z_band: number; order_in_band: number; visible: boolean; locked: boolean }>
  series_sources: PersistedSeriesSource[]
  strategy_sources: PersistedStrategySource[]
  updated_at: string
}
