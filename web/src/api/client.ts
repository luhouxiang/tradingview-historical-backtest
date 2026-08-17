import type {
  DatasetMeta,
  DatasetSummary,
  BarRangeResponse,
  ErrorResponse,
  HealthResponse,
  JobAccepted,
  JobStatus,
  SourceFile,
  AlgorithmDefinition,
  CalculationRequest,
  CalculationResults,
  WorkspaceLayout,
  StrategySourceDynamicConfig,
  ReplayRequest,
  ReplayStatus,
  ReplayEventsResponse,
  CausalEvent,
  BacktestRequest,
  RunAccepted,
  RunStatus,
  BacktestSummary,
  BacktestTrade,
  EquityRow,
  StudyRequest,
  StudyAccepted,
  StudyStatus,
  StudyEvaluations,
} from '../types/api'

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly requestId: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message)
  }
}

function identifier(): string {
  return crypto.randomUUID().replaceAll('-', '')
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('X-Request-ID', identifier())
  headers.set('X-Trace-ID', identifier())
  if (init.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })
  const payload = (await response.json()) as T | ErrorResponse
  if (!response.ok) {
    const failure = payload as ErrorResponse
    throw new ApiError(
      failure.error?.code ?? 'HTTP_ERROR',
      failure.error?.message ?? response.statusText,
      failure.error?.request_id ?? response.headers.get('X-Request-ID') ?? '',
      failure.error?.details,
    )
  }
  return payload as T
}

export function getHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/v1/health')
}

export function startDatasetScan(): Promise<JobAccepted> {
  return apiRequest<JobAccepted>('/api/v1/datasets/scan', { method: 'POST' })
}

export function getJob(jobId: string): Promise<JobStatus> {
  return apiRequest<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}`)
}

export async function getSourceFiles(): Promise<SourceFile[]> {
  const response = await apiRequest<{ request_id: string; items: SourceFile[] }>('/api/v1/source-files')
  return response.items
}

export function importSource(source: SourceFile): Promise<JobAccepted> {
  const detected = source.detected ?? {}
  return apiRequest<JobAccepted>('/api/v1/datasets/import', {
    method: 'POST',
    body: JSON.stringify({
      source_file_id: source.source_file_id,
      importer_id: 'tdx_txt_v1',
      exchange: detected.exchange,
      instrument: detected.symbol,
      timeframe: detected.timeframe,
      date_semantics: detected.date_semantics ?? 'trading_day',
      timezone: detected.timezone ?? 'Asia/Shanghai',
      timestamp_semantics: detected.timestamp_semantics ?? 'bar_end',
    }),
  })
}

export async function listDatasets(): Promise<{ catalog_revision: number; datasets: DatasetSummary[] }> {
  return apiRequest('/api/v1/datasets')
}

export function getDataset(datasetId: string, revision: string): Promise<DatasetMeta> {
  const query = new URLSearchParams({ revision })
  return apiRequest(`/api/v1/datasets/${encodeURIComponent(datasetId)}?${query}`)
}

export function getBars(
  datasetId: string,
  revision: string,
  generationId: string,
  range: { tail?: number; beforeBarIndex?: number; limit?: number } = {},
): Promise<BarRangeResponse> {
  const query = new URLSearchParams({ revision, generation_id: generationId })
  if (range.tail !== undefined) query.set('tail', String(range.tail))
  if (range.beforeBarIndex !== undefined) query.set('before_bar_index', String(range.beforeBarIndex))
  if (range.limit !== undefined) query.set('limit', String(range.limit))
  return apiRequest(`/api/v1/datasets/${encodeURIComponent(datasetId)}/bars?${query}`)
}

export async function listAlgorithms(): Promise<AlgorithmDefinition[]> {
  const response = await apiRequest<{ request_id: string; algorithms: AlgorithmDefinition[] }>('/api/v1/algorithms')
  return response.algorithms
}

export function createCalculation(request: CalculationRequest): Promise<JobAccepted | JobStatus> {
  return apiRequest('/api/v1/calculations', { method: 'POST', body: JSON.stringify(request) })
}

export function getCalculation(jobId: string): Promise<JobStatus> {
  return apiRequest(`/api/v1/calculations/${encodeURIComponent(jobId)}`)
}

export function getCalculationResults(jobId: string, fromBarIndex: number, toBarIndex: number): Promise<CalculationResults> {
  const query = new URLSearchParams({ from_bar_index: String(fromBarIndex), to_bar_index: String(toBarIndex) })
  return apiRequest(`/api/v1/calculations/${encodeURIComponent(jobId)}/results?${query}`)
}

export function createReplay(request: ReplayRequest): Promise<ReplayStatus> {
  return apiRequest('/api/v1/replays', { method: 'POST', body: JSON.stringify(request) })
}

export function getReplay(replayId: string): Promise<ReplayStatus> {
  return apiRequest(`/api/v1/replays/${encodeURIComponent(replayId)}`)
}

export function getReplayEvents(replayId: string, knownFromBarIndex: number, knownToBarIndex: number): Promise<ReplayEventsResponse> {
  const query = new URLSearchParams({
    known_from_bar_index: String(knownFromBarIndex),
    known_to_bar_index: String(knownToBarIndex),
  })
  return apiRequest(`/api/v1/replays/${encodeURIComponent(replayId)}/events?${query}`)
}

export function createBacktest(request: BacktestRequest, idempotencyKey = identifier()): Promise<RunAccepted> {
  return apiRequest('/api/v1/backtests', {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(request),
  })
}

export function getBacktest(runId: string): Promise<RunStatus> {
  return apiRequest(`/api/v1/backtests/${encodeURIComponent(runId)}`)
}

export function getBacktestSummary(runId: string): Promise<BacktestSummary> {
  return apiRequest(`/api/v1/backtests/${encodeURIComponent(runId)}/summary`)
}

export async function getBacktestTrades(runId: string, cursor?: string): Promise<{ rows: BacktestTrade[]; next_cursor: string | null }> {
  const query = new URLSearchParams()
  if (cursor) query.set('cursor', cursor)
  return apiRequest(`/api/v1/backtests/${encodeURIComponent(runId)}/trades?${query}`)
}

export async function getBacktestEquity(runId: string): Promise<EquityRow[]> {
  const response = await apiRequest<{ request_id: string; run_id: string; rows: EquityRow[] }>(`/api/v1/backtests/${encodeURIComponent(runId)}/equity`)
  return response.rows
}

export async function getBacktestChartEvents(runId: string): Promise<CausalEvent[]> {
  const response = await apiRequest<{ request_id: string; run_id: string; events: CausalEvent[] }>(`/api/v1/backtests/${encodeURIComponent(runId)}/chart-events`)
  return response.events
}

export function createStudy(request: StudyRequest): Promise<StudyAccepted> {
  return apiRequest('/api/v1/studies', { method: 'POST', body: JSON.stringify(request) })
}

export function getStudy(studyId: string): Promise<StudyStatus> {
  return apiRequest(`/api/v1/studies/${encodeURIComponent(studyId)}`)
}

export function getStudyEvaluations(studyId: string): Promise<StudyEvaluations> {
  return apiRequest(`/api/v1/studies/${encodeURIComponent(studyId)}/evaluations`)
}

export function getLayout(profileId: string, layoutId: string): Promise<WorkspaceLayout> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/layouts/${encodeURIComponent(layoutId)}`)
}

export function putLayout(profileId: string, layoutId: string, expectedRevision: number, document: WorkspaceLayout): Promise<WorkspaceLayout> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/layouts/${encodeURIComponent(layoutId)}`, {
    method: 'PUT', headers: { 'If-Match': String(expectedRevision) }, body: JSON.stringify(document),
  })
}

export function getStrategySourceConfig(profileId: string): Promise<StrategySourceDynamicConfig> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/strategy-source-config`)
}

export function putStrategySourceConfig(profileId: string, expectedRevision: number, document: StrategySourceDynamicConfig): Promise<StrategySourceDynamicConfig> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/strategy-source-config`, {
    method: 'PUT', headers: { 'If-Match': String(expectedRevision) }, body: JSON.stringify(document),
  })
}

export interface DrawingDocument<T> {
  request_id?: string
  schema_version: 1
  profile_id: string
  layout_id: string
  dataset_id: string
  data_revision: string
  revision: number
  drawings: T[]
  updated_at: string
}

export function getDrawings<T>(profileId: string, layoutId: string, datasetId: string): Promise<DrawingDocument<T>> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/drawings/${encodeURIComponent(layoutId)}/${encodeURIComponent(datasetId)}`)
}

export function putDrawings<T>(profileId: string, layoutId: string, datasetId: string, expectedRevision: number, document: DrawingDocument<T>): Promise<DrawingDocument<T>> {
  return apiRequest(`/api/v1/workspaces/${encodeURIComponent(profileId)}/drawings/${encodeURIComponent(layoutId)}/${encodeURIComponent(datasetId)}`, {
    method: 'PUT', headers: { 'If-Match': String(expectedRevision) }, body: JSON.stringify(document),
  })
}
