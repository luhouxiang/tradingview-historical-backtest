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
  first_trading_day?: string
  last_trading_day?: string
  trading_day_count?: number
  independence_group?: string
  status: 'ready' | 'importing' | 'invalid'
}

export interface DatasetMeta {
  request_id: string
  dataset_id: string
  data_revision: string
  independence_group?: string
  instrument: { exchange: string; symbol: string; product: string; contract_multiplier: number; display_name?: string }
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
    trading_day_count?: number
  }
  quality: Record<string, number>
}

export interface DatasetResearchReadiness {
  request_id: string
  status: 'exploratory' | 'certification_ready'
  required_trading_days: 504
  required_independence_groups: 3
  eligible_independence_group_count: number
  datasets: Array<{
    dataset_id: string
    data_revision: string
    independence_group: string
    trading_day_count: number
    eligible: boolean
    overlapping_dataset_ids: string[]
  }>
  reasons: string[]
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
  kind: 'indicator' | 'chan' | 'strategy' | 'risk_filter'
  algorithm_id: string
  algorithm_version: string
  source_hash: string
}

export interface AlgorithmOutput {
  name: string
  display_name: string
  pane: 'main' | 'indicator'
  series_type: 'line' | 'histogram' | 'semantic_objects'
  object_type?: 'processed_bar' | 'fractal' | 'bi' | 'bi_state' | 'segment' | 'zhongshu' | 'segment_zhongshu' | 'level_center' | 'level_movement' | 'movement_state' | 'center_monitor' | 'divergence' | 'trade_point' | 'strategy_state' | 'stage_signal' | 'trade_signal' | 'chart_event' | 'risk_decision'
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
  warmup: { kind: 'formula'; expression: string } | { kind: 'fixed_bars'; bars: number }
  causal: true
  comparison_eligible?: boolean
  research_role?: 'formal_strategy' | 'example_strategy' | 'auxiliary_non_trading' | 'risk_filter'
  strategy_family?: string
  catalog_algorithm_ids?: string[]
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
  zone_low_i64: number
  zone_high_i64: number
  extreme_source_bar_index: number
  fractal_type: 'top' | 'bottom'
  status: 'candidate' | 'confirmed' | 'invalidated'
  invalidation_reason: string | null
  aux_strength: 'strong_reversal' | 'unclassified'
  strength_reason: string
  catalog_algorithm_id: 'ALG-GEO-002'
  strength_semantic_namespace: 'auxiliary'
  standard_signal: false
  execution_allowed: false
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
  start_extreme_source_bar_index: number
  end_bar_index: number
  end_time: number
  end_price_i64: number
  end_extreme_source_bar_index: number
  direction: 'up' | 'down'
  status: 'candidate' | 'confirmed' | 'invalidated'
  invalidation_reason: string | null
  catalog_algorithm_id: 'ALG-GEO-003' | 'ALG-GEO-004'
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
  gg_i64: number
  dd_i64: number
  z_i64: number
  analysis_level: string
  component_kind: 'bi' | 'segment'
  component_count: number
  confirmed: boolean
  confirmed_at_bar_index: number | null
  status: 'confirmed' | 'extended' | 'left'
  leave_direction: 'up' | 'down' | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanSignalPoint {
  object_id: string
  bar_index: number
  time: number
  price_i64: number
  signal_type: 'bottom_divergence' | 'top_divergence' | 'buy_1' | 'buy_2' | 'buy_3' | 'sell_1' | 'sell_2' | 'sell_3' | 'class_buy_1' | 'class_buy_2' | 'class_buy_3' | 'class_sell_1' | 'class_sell_2' | 'class_sell_3'
  divergence_kind: 'trend' | 'consolidation' | null
  signal_class: 'standard' | 'class_like' | null
  strength: 'strongest' | 'normal' | 'weakest' | null
  reference_object_id: string | null
  macd_area_reference: number | null
  macd_area_current: number | null
  status: 'candidate' | 'confirmed' | 'invalidated'
  invalidation_reason: string | null
  level_id: string | null
  lower_level_turn_object_id: string | null
  catalog_event: 'B1_candidate' | 'B1_confirmed' | 'B1_invalidated' | 'S1_candidate' | 'S1_confirmed' | 'S1_invalidated' | null
  catalog_algorithm_id: 'ALG-SIG-001' | null
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanProcessedBar {
  object_id: string
  normalized_index: number
  start_bar_index: number
  start_time: number
  end_bar_index: number
  end_time: number
  open_i64: number
  high_i64: number
  low_i64: number
  close_i64: number
  high_source_bar_index: number
  low_source_bar_index: number
  direction: 'up' | 'down' | 'unknown'
  source_bar_indices: number[]
  status: 'forming' | 'sealed'
  sealed_at_bar_index: number | null
  catalog_event: 'processed_bar' | 'processed_bar_revision'
  known_at_bar_index: number
  object_revision: number
}

export interface ChanBiState {
  object_id: string
  bar_index: number
  time: number
  price_i64: number
  state: 'SEEK_FIRST_FRACTAL' | 'UP_EXTENDING' | 'TOP_FORMING' | 'DOWN_EXTENDING' | 'BOTTOM_FORMING'
  direction: 'up' | 'down' | null
  anchor_fractal_id: string | null
  candidate_object_id: string | null
  trigger: 'initial' | 'processed_bar_update' | 'first_fractal_confirmed' | 'fractal_confirmed' | 'candidate_started' | 'candidate_revised' | 'candidate_invalidated' | 'bi_confirmed'
  catalog_algorithm_id: 'ALG-GEO-003'
  known_at_bar_index: number
  object_revision: number
}

export interface ChanTreeObject {
  object_id: string
  object_type?: 'bi_state' | 'level_center' | 'level_movement' | 'movement_state' | 'center_monitor' | 'divergence' | 'trade_point'
  bar_index: number
  time: number
  price_i64: number
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
  label?: string
  detail?: string
  signal?: ChanSignalPoint
}

export interface StrategyRunSource {
  source_type: 'StrategyRunSource'
  source_id: string
  run_id: string
  definition: AlgorithmDefinition
  status: 'completed'
  visible: boolean
  objects: ChanTreeObject[]
  signals: Array<Record<string, unknown> & { object_type: string; object_id: string }>
}

export interface ChanLevelCenter {
  object_id: string
  level_id: string
  parent_level_id: string
  start_bar_index: number
  start_time: number
  end_bar_index: number
  end_time: number
  zd_i64: number
  zg_i64: number
  dd_i64: number
  gg_i64: number
  component_kind: 'segment' | 'sublevel_movement'
  component_object_ids: string[]
  source_center_ids: string[]
  status: 'candidate' | 'confirmed' | 'extended' | 'terminated' | 'promoted' | 'superseded'
  promotion_reason: 'nine_component_extension' | 'overlapping_fluctuation_ranges'
  promoted_from_center_id: string | null
  catalog_event: 'center_candidate' | 'center_confirmed' | 'center_extended' | 'center_promoted' | 'center_terminated'
  catalog_algorithm_id: 'ALG-GEO-005'
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanLevelMovement {
  object_id: string
  level_id: string
  start_bar_index: number
  start_time: number
  end_bar_index: number
  end_time: number
  low_i64: number
  high_i64: number
  component_center_ids: string[]
  classification: 'consolidation' | 'uptrend' | 'downtrend' | 'higher_level_center_candidate'
  direction: 'up' | 'down' | null
  status: 'candidate' | 'confirmed' | 'reclassified' | 'invalidated'
  previous_classification: 'consolidation' | 'uptrend' | 'downtrend' | 'higher_level_center_candidate' | null
  reclassification_reason: string | null
  parent_center_candidate_id: string | null
  catalog_event: 'movement_candidate' | 'movement_confirmed' | 'movement_reclassified'
  catalog_algorithm_id: 'ALG-GEO-006'
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanMovementState {
  object_id: string
  start_bar_index: number
  start_time: number
  end_bar_index: number
  end_time: number
  price_i64: number
  state_type: 'consolidation' | 'centre_oscillation' | 'centre_migration_up' | 'centre_migration_down'
  direction: 'up' | 'down' | null
  analysis_level: string
  reference_object_id: string
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanCenterMonitor {
  object_id: string
  bar_index: number
  time: number
  z_i64: number
  zn_i64: number
  z_twice_i64: number
  zn_twice_i64: number
  core_low_i64: number
  core_high_i64: number
  range_high_i64: number
  range_low_i64: number
  component_ordinal: number
  component_direction: 'up' | 'down'
  relative_position: 'above' | 'below' | 'equal'
  oscillation_bias: 'strong' | 'weak' | 'neutral'
  breakout_warning: 'cross_above_b' | 'cross_below_a' | 'rising_wedge_below_b' | 'falling_wedge_above_a' | null
  catalog_algorithm_id: 'ALG-AUX-004'
  semantic_namespace: 'auxiliary'
  evidence_level: 'AUXILIARY'
  level_mapping_profile: 'segment_center_components_v1'
  standard_signal: false
  execution_allowed: false
  confirms_third_point: false
  analysis_level: string
  reference_object_id: string
  confirmed: boolean
  confirmed_at_bar_index: number | null
  known_at_bar_index: number
  object_revision: number
}

export interface ChanCalculationResults extends CalculationResultBase {
  result_kind: 'chan'
  objects: {
    processed_bars: ChanProcessedBar[]
    fractals: ChanFractal[]
    bi: ChanLineObject[]
    bi_states: ChanBiState[]
    segments: ChanLineObject[]
    zhongshu: ChanZhongshu[]
    segment_zhongshu: ChanZhongshu[]
    level_centers: ChanLevelCenter[]
    level_movements: ChanLevelMovement[]
    movement_states: ChanMovementState[]
    center_monitors: ChanCenterMonitor[]
    divergences: ChanSignalPoint[]
    trade_points: ChanSignalPoint[]
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
  object_type: 'processed_bar' | 'fractal' | 'bi' | 'bi_state' | 'segment' | 'zhongshu' | 'segment_zhongshu' | 'level_center' | 'level_movement' | 'movement_state' | 'center_monitor' | 'divergence' | 'trade_point' | 'strategy_state' | 'stage_signal' | 'trade_signal' | 'chart_event' | 'risk_decision'
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
  ranking_context?: RankingContext
  risk_overlay?: RiskOverlay
  range: { warmup_from_bar_index: number; from_bar_index: number; to_bar_index: number }
  execution: {
    semantic_version: '1.0.0'
    signal_timing: 'bar_close'
    fill_timing: 'next_bar_open' | 'bar_close'
    commission: Record<string, string | number>
    slippage: { mode: 'ticks' | 'bps'; value: number }
    contract_multiplier?: number
    contract_multiplier_source?: 'instrument_config'
    margin_ratio: number
    intrabar_conflict_rule: 'stop_first' | 'target_first' | 'worst_case'
    stress_scenario_id?: string
    cost_multiplier?: number
    additional_slippage_ticks?: number
    additional_delay_bars?: number
    max_volume_participation_rate?: number
    fill_mode?: 'unlimited' | 'volume_cap_ioc'
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
  annualized_return_reason?: string | null
  max_drawdown: number
  sharpe: number | null
  sharpe_reason?: string | null
  annualized_volatility?: number | null
  annualized_volatility_reason?: string | null
  trading_day_count?: number
  daily_return_count?: number
  trade_count: number
  win_rate: number | null
  average_win_i64: number | null
  average_loss_i64: number | null
  profit_loss_ratio: number | null
  profit_factor: number | null
  expectancy_i64: number | null
  total_commission_i64: number
  total_slippage_i64: number
  requested_quantity?: number
  filled_quantity?: number
  fill_rate?: number | null
  risk_approved_count: number
  risk_reduced_count: number
  risk_blocked_count: number
  risk_kill_switch_count: number
}

export interface BacktestTrade {
  trade_id: string
  side: 'long' | 'short'
  entry_bar_index: number
  entry_time: number
  entry_price_i64: number
  entry_signal_id: string
  entry_signal_known_at_bar_index: number
  entry_order_id: string
  exit_bar_index: number
  exit_time: number
  exit_price_i64: number
  exit_signal_id?: string
  exit_order_id?: string
  quantity: number
  gross_pnl_i64: number
  net_pnl_i64: number
  commission_i64: number
  slippage_i64: number
  market_l0?: 'uptrend' | 'downtrend' | 'consolidation' | 'higher_level_center_candidate' | 'unknown'
  center_phase?: 'consolidation' | 'center_oscillation' | 'migrating_up' | 'migrating_down' | 'unknown'
  price_vs_center?: 'above' | 'inside' | 'below' | 'unknown'
  trigger_category?: 'B1' | 'B2' | 'B3' | 'S1' | 'S2' | 'S3' | 'class_buy_sell' | 'other'
  structure_object_id?: string
  structure_object_revision?: number
  attribution_reason_code?: string
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
  risk_overlay?: RiskOverlay
}

export interface StrategyComparisonRequest {
  dataset_id: string
  data_revision: string
  strategies: Array<{
    strategy: AlgorithmRef
    parameters: Record<string, string | number | boolean>
  }>
  risk_overlay?: RiskOverlay
  range: BacktestRequest['range']
  execution: BacktestRequest['execution']
  capital: BacktestRequest['capital']
  random_seed: number
  minimum_trade_count: number
}

export interface StrategyComparisonAccepted {
  request_id: string
  comparison_id: string
  status: 'queued'
}

export interface StrategyComparisonStatus {
  request_id: string
  comparison_id: string
  status: JobStatus['status']
  progress: number
  total_count: number
  completed_count: number
  failed_count: number
  current_algorithm_id: string | null
  result_ref?: string
  manifest?: StrategyComparisonManifest
  error?: { code: string; message: string }
}

export interface StrategyComparisonManifest {
  schema_version: 2
  comparison_id: string
  comparison_signature: string
  aggregator_version: string
  trace_id: string
  dataset: { dataset_id: string; data_revision: string }
  range: BacktestRequest['range']
  execution: Record<string, unknown>
  capital: Record<string, unknown>
  random_seed: number
  minimum_trade_count: number
  strategies: Array<{ strategy: AlgorithmRef; parameters: Record<string, string | number | boolean> }>
  strategy_count: number
  completed_count: number
  failed_count: number
  created_at: string
}

export interface StrategyComparisonResult {
  algorithm_id: string
  name: string
  strategy_family: string
  parameters: Record<string, string | number | boolean>
  status: 'completed' | 'failed' | 'skipped'
  run_id?: string
  run_signature?: string
  tier?: 'failed' | 'no_trades' | 'loss_making' | 'profitable_low_sample' | 'profitable_candidate' | 'pareto_candidate'
  pareto?: boolean
  summary?: BacktestSummary
  attribution?: {
    attribution_supported: boolean
    realized_pnl_i64: number
    dimensions: Array<{
      dimension: string; value: string; trade_count: number; win_rate: number | null
      realized_net_pnl_i64: number; expectancy_i64: number | null; profit_factor: number | null
      average_holding_bars: number; commission_i64: number; slippage_i64: number
    }>
  } | null
  error?: { code: string; message: string }
}

export interface ResearchStudyRequest {
  datasets: Array<{ dataset_id: string; data_revision: string; range: BacktestRequest['range'] }>
  strategy: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  execution: BacktestRequest['execution']
  capital: BacktestRequest['capital']
  random_seed: number
  walk_forward?: WalkForwardConfig
  stress_test?: StressTestConfig
  statistical_validation?: StatisticalValidationConfig
}

export interface StressTestConfig {
  suite_version: '1.0.0'
  volume_participation_rate: number
}

export interface StatisticalValidationConfig {
  method_version: '1.0.0'
  block_size_trading_days: number
  iterations: number
  confidence_level: 0.95
  random_seed: number
  holm_alpha: 0.05
}

export interface ResearchEvidenceGate {
  gate_id: string
  required_for: 'research_candidate' | 'reliable_candidate'
  passed: boolean
  actual: unknown
  threshold: unknown
  reason: string
}

export interface ResearchCertification {
  rules_version: string
  tier: 'exploratory' | 'research_candidate' | 'reliable_candidate'
  reliable_candidate_is_historical_only: true
  research_candidate_passed: boolean
  reliable_candidate_passed: boolean
  reasons: string[]
  evidence_matrix: ResearchEvidenceGate[]
}

export interface StressScenarioResult {
  scenario_id: string
  status: 'completed' | 'failed'
  cost_multiplier: number
  additional_slippage_ticks: number
  additional_delay_bars: number
  max_volume_participation_rate: number | null
  fill_mode: 'unlimited' | 'volume_cap_ioc'
  completed_run_count: number
  failed_run_count: number
  daily_return_count?: number
  total_return: number | null
  max_drawdown: number | null
  trade_count: number
  requested_quantity: number
  filled_quantity: number
  fill_rate: number | null
  return_degradation: number | null
  drawdown_degradation: number | null
  fill_rate_degradation: number | null
  failure_reason: string | null
}

export interface WalkForwardConfig {
  train_trading_days: number
  validation_trading_days: number
  step_trading_days: number
  search_space: Array<{
    name: string
    type: 'integer' | 'number' | 'boolean' | 'string'
    candidates: Array<string | number | boolean>
  }>
  objectives: Array<{ metric: StudyMetric; direction: 'maximize' | 'minimize' }>
  constraints: Array<{ metric: StudyMetric; operator: '>=' | '<='; value: number }>
  search: { method: 'grid' | 'random'; budget: number; random_seed: number }
}

export interface ResearchStudyAccepted {
  request_id: string
  research_study_id: string
  status: 'queued' | 'running'
}

export interface ResearchStudyAggregate {
  completed_dataset_count: number
  failed_dataset_count: number
  independence_group_count: number
  eligible_independence_group_count: number
  data_status: 'exploratory' | 'certification_ready'
  daily_return_count: number
  total_trade_count: number
  total_return: number
  annualized_return: number | null
  sharpe: number | null
  annualized_volatility: number | null
  max_drawdown: number
  median_dataset_return: number | null
  worst_dataset_id: string | null
  worst_dataset_return: number | null
  profitable_dataset_ratio: number | null
  worst_dataset_max_drawdown: number
  walk_forward_fold_count?: number
  completed_walk_forward_fold_count?: number
  profitable_fold_ratio?: number | null
  worst_fold_max_drawdown?: number | null
  out_of_sample_trade_count?: number
  parameter_stability?: number | null
  stress_scenarios?: StressScenarioResult[]
  first_failure_scenario?: string | null
  attempted_parameter_combinations?: Array<{ combination_id: string; parameters: Record<string, string | number | boolean>; attempt_count: number; completed_count: number }>
  statistical_evidence?: {
    bootstrap?: {
      method: string; sample_count: number; block_size_trading_days: number; iterations: number
      confidence_level: number; random_seed: number
      metrics: Record<string, { point_estimate: number | null; lower: number | null; upper: number | null; reason: string | null }>
    }
    multiple_comparisons?: { candidate_count: number; comparison_count: number; multiple_comparison_warning: boolean; warning: string | null; comparisons: Array<Record<string, unknown>> }
    parameter_neighborhood?: { evaluated_neighbor_count: number; completed_neighbor_count: number; pass_rate: number | null; required_pass_rate: number; passed: boolean; reason: string | null }
    certification?: ResearchCertification
  }
  certification?: ResearchCertification
  certification_trade_count?: number
  out_of_sample_expectancy_i64?: number | null
  minimum_completed_folds_per_group?: number
  minimum_studied_trading_days_per_group?: number
}

export interface ResearchStudyManifest {
  schema_version: 1
  research_study_id: string
  study_signature: string
  aggregator_version: string
  trace_id: string
  timeframe: string
  strategy: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  datasets: Array<{ dataset_id: string; data_revision: string; independence_group: string; trading_day_count: number; range: BacktestRequest['range']; execution?: Record<string, unknown>; run_id?: string; run_signature?: string }>
  execution: Record<string, unknown>
  capital: Record<string, unknown>
  random_seed: number
  study_mode: 'fixed_parameters' | 'walk_forward' | 'walk_forward_stress' | 'walk_forward_certification'
  walk_forward?: WalkForwardConfig
  stress_test?: StressTestConfig
  statistical_validation?: StatisticalValidationConfig
  child_runs: Array<{ dataset_id: string; run_id: string; run_signature: string; role: 'fixed' | 'train_candidate' | 'validation' | 'stress' | 'neighbor'; fold_index?: number; candidate_index?: number; scenario_id?: string; parameter_name?: string; neighbor_direction?: 'lower' | 'upper' }>
  artifacts?: { out_of_sample_daily_returns?: string; stress_results?: string; statistical_evidence?: string }
  aggregate: ResearchStudyAggregate
  created_at: string
}

export interface ResearchStudyStatus {
  request_id: string
  research_study_id: string
  status: JobStatus['status']
  progress: number
  progress_detail?: ResearchStudyProgressDetail
  result_ref?: string
  manifest?: ResearchStudyManifest
  error?: { code: string; message: string }
}

export interface ResearchStudyProgressDetail {
  stage: 'dataset_backtests' | 'walk_forward' | 'stress_test' | 'bootstrap' | 'parameter_neighborhood' | 'aggregation' | 'committing'
  completed_count: number
  total_count: number
  current_dataset_id?: string | null
  current_scenario_id?: string | null
  current_fold_index?: number | null
}

export interface ResearchDatasetResult {
  dataset_id: string
  data_revision: string
  independence_group: string
  trading_day_count: number
  status: 'completed' | 'failed'
  run_id?: string
  run_signature?: string
  summary?: BacktestSummary
  error?: { code: string; message: string }
  folds?: WalkForwardFoldResult[]
  walk_forward_summary?: Record<string, number | null>
}

export interface WalkForwardFoldResult {
  dataset_id: string
  independence_group: string
  fold_index: number
  status: 'completed' | 'failed'
  train_trading_day_from?: string
  train_trading_day_to?: string
  validation_trading_day_from?: string
  validation_trading_day_to?: string
  train_range: BacktestRequest['range']
  validation_range: BacktestRequest['range']
  selected_parameters?: Record<string, string | number | boolean>
  training_ranking?: Array<Record<string, unknown>>
  selected_train_metrics?: BacktestSummary
  validation_metrics?: BacktestSummary
  selected_train_run_id?: string
  selected_train_run_signature?: string
  validation_run_id?: string
  validation_run_signature?: string
  parameter_changed?: boolean
  changed_parameter_names?: string[]
  error?: { code: string; message: string }
}

export interface ResearchStudyResults {
  items: ResearchDatasetResult[]
  aggregate: ResearchStudyAggregate
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

export interface RankingMembership {
  dataset_id: string
  data_revision: string
  sector_id: string
  effective_from_utc: number
  effective_to_utc: number | null
  available_at_utc: number
}

export interface RankingContext {
  universe_id: string
  membership_revision: string
  membership_mode: 'point_in_time'
  price_adjustment_mode: 'forward_adjusted' | 'back_adjusted' | 'total_return'
  price_adjustment_revision: string
  episode_id: string
  episode_start_timestamp_utc: number
  episode_available_at_utc: number
  memberships: RankingMembership[]
}

export interface RiskMarketObservation {
  effective_from_bar_index: number
  available_at_bar_index: number
  data_revision: string
  trading_status: 'normal' | 'suspended' | 'limit_up' | 'limit_down'
  stale_bars: number
  data_gap_bars: number
  event_risk_active: boolean
}

export interface RiskContext {
  market_state_revision: string
  sector_id: string
  legal_future_branches: string[]
  handled_future_branches: string[]
  observations: RiskMarketObservation[]
}

export interface RiskOverlay {
  algorithm: AlgorithmRef
  parameters: Record<string, string | number | boolean>
  context: RiskContext
}

export interface StrategySource {
  source_type: 'StrategySource'
  source_id: string
  definition: AlgorithmDefinition
  parameters: Record<string, string | number | boolean>
  job_id: string
  status: JobStatus['status']
  visible: boolean
  category_visibility: { processed_bars?: boolean; fractals: boolean; bi: boolean; bi_states?: boolean; segments: boolean; zhongshu: boolean; segment_zhongshu: boolean; level_centers?: boolean; level_movements?: boolean; movement_states?: boolean; center_monitors?: boolean; divergences: boolean; trade_points: boolean }
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
  category_visibility: { processed_bars?: boolean; fractals: boolean; bi: boolean; bi_states?: boolean; segments?: boolean; zhongshu: boolean; segment_zhongshu?: boolean; level_centers?: boolean; level_movements?: boolean; movement_states?: boolean; center_monitors?: boolean; divergences?: boolean; trade_points?: boolean }
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
  bottom_panel: { height: number; collapsed: boolean; active_tab: 'replay' | 'backtest' | 'trades' | 'equity' | 'optimization' | 'research' | 'tasks' | 'logs' }
  object_order: Array<{ id: string; pane_id: string; z_band: number; order_in_band: number; visible: boolean; locked: boolean }>
  series_sources: PersistedSeriesSource[]
  strategy_sources: PersistedStrategySource[]
  updated_at: string
}

export interface StrategySourcePreference {
  dataset_id: string
  data_revision: string
  source_id: string
  visible: boolean
  category_visibility: Required<StrategySource['category_visibility']>
}

export interface StrategySourceDynamicConfig {
  request_id?: string
  schema_version: 1
  profile_id: string
  revision: number
  strategy_sources: StrategySourcePreference[]
  updated_at: string
}
