import type { Plugin, ViteDevServer } from 'vite'

export interface FullStackReadyPluginOptions {
  healthUrl: string
  pageUrl: string
  pollIntervalMs?: number
  requestTimeoutMs?: number
  checkReady?: () => Promise<boolean>
}

export interface DemoDatasetReadyOptions {
  apiBaseUrl: string
  fetchImpl?: typeof fetch
  jobPollIntervalMs?: number
  jobTimeoutMs?: number
  requestTimeoutMs?: number
}

interface JobStatus {
  job_id: string
  status: string
  error?: { message?: string }
}

interface SourceFile {
  source_file_id: string
  status: string
  detected?: {
    exchange?: string
    symbol?: string
    timeframe?: string
    date_semantics?: string
    timezone?: string
    timestamp_semantics?: string
  }
}

async function jsonRequest<T>(
  fetchImpl: typeof fetch,
  url: string,
  init: RequestInit | undefined,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetchImpl(url, { ...init, signal: controller.signal })
    if (!response.ok) throw new Error(`Request failed with HTTP ${response.status}: ${url}`)
    return response.json() as Promise<T>
  } finally {
    clearTimeout(timeout)
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function waitForJob(
  fetchImpl: typeof fetch,
  apiBaseUrl: string,
  jobId: string,
  pollIntervalMs: number,
  timeoutMs: number,
  requestTimeoutMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const job = await jsonRequest<JobStatus>(
      fetchImpl,
      `${apiBaseUrl}/api/v1/jobs/${encodeURIComponent(jobId)}`,
      undefined,
      requestTimeoutMs,
    )
    if (job.status === 'completed') return
    if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
      throw new Error(job.error?.message ?? `Job ${jobId} ended as ${job.status}`)
    }
    await delay(pollIntervalMs)
  }
  throw new Error(`Timed out waiting for job ${jobId}`)
}

export function createDemoDatasetReadyCheck(options: DemoDatasetReadyOptions): () => Promise<boolean> {
  const fetchImpl = options.fetchImpl ?? fetch
  const apiBaseUrl = options.apiBaseUrl.replace(/\/$/, '')
  const pollIntervalMs = options.jobPollIntervalMs ?? 250
  const timeoutMs = options.jobTimeoutMs ?? 120000
  const requestTimeoutMs = options.requestTimeoutMs ?? 2000
  let bootstrapAttempted = false

  return async (): Promise<boolean> => {
    try {
      const health = await jsonRequest<{ status?: string }>(
        fetchImpl,
        `${apiBaseUrl}/api/v1/health`,
        undefined,
        requestTimeoutMs,
      )
      if (health.status !== 'ok') return false

      let catalog = await jsonRequest<{ datasets?: unknown[] }>(
        fetchImpl,
        `${apiBaseUrl}/api/v1/datasets`,
        undefined,
        requestTimeoutMs,
      )
      if ((catalog.datasets?.length ?? 0) > 0) return true
      if (bootstrapAttempted) return false

      const scan = await jsonRequest<{ job_id: string }>(fetchImpl, `${apiBaseUrl}/api/v1/datasets/scan`, {
        method: 'POST',
      }, requestTimeoutMs)
      bootstrapAttempted = true
      await waitForJob(fetchImpl, apiBaseUrl, scan.job_id, pollIntervalMs, timeoutMs, requestTimeoutMs)

      catalog = await jsonRequest<{ datasets?: unknown[] }>(
        fetchImpl,
        `${apiBaseUrl}/api/v1/datasets`,
        undefined,
        requestTimeoutMs,
      )
      if ((catalog.datasets?.length ?? 0) > 0) return true

      const sourceResponse = await jsonRequest<{ items?: SourceFile[] }>(
        fetchImpl,
        `${apiBaseUrl}/api/v1/source-files`,
        undefined,
        requestTimeoutMs,
      )
      const source = sourceResponse.items?.find((item) => (
        item.status === 'importable' && item.detected?.symbol === 'AO2609'
      ))
      if (!source?.detected) return false

      const detected = source.detected
      const imported = await jsonRequest<{ job_id: string }>(fetchImpl, `${apiBaseUrl}/api/v1/datasets/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
      }, requestTimeoutMs)
      await waitForJob(fetchImpl, apiBaseUrl, imported.job_id, pollIntervalMs, timeoutMs, requestTimeoutMs)
      catalog = await jsonRequest<{ datasets?: unknown[] }>(
        fetchImpl,
        `${apiBaseUrl}/api/v1/datasets`,
        undefined,
        requestTimeoutMs,
      )
      return (catalog.datasets?.length ?? 0) > 0
    } catch {
      return false
    }
  }
}

async function requestFullStackHealth(healthUrl: string, requestTimeoutMs: number): Promise<boolean> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), requestTimeoutMs)
  try {
    const response = await fetch(healthUrl, { signal: controller.signal })
    if (!response.ok) return false
    const payload = (await response.json()) as { status?: string }
    return payload.status === 'ok'
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

export function fullStackReadyPlugin(options: FullStackReadyPluginOptions): Plugin {
  const pollIntervalMs = options.pollIntervalMs ?? 250
  const requestTimeoutMs = options.requestTimeoutMs ?? 1000
  const checkReady = options.checkReady
    ?? (() => requestFullStackHealth(options.healthUrl, requestTimeoutMs))

  return {
    name: 'tvbt-full-stack-ready',
    apply: 'serve',
    configureServer(server: ViteDevServer) {
      let stopped = false
      let retryTimer: ReturnType<typeof setTimeout> | undefined

      const poll = async (): Promise<void> => {
        if (stopped) return
        let ready = false
        try { ready = await checkReady() } catch { ready = false }
        if (ready) {
          server.config.logger['info'](`TVBT full stack ready: ${options.pageUrl}`)
          return
        }
        if (!stopped) retryTimer = setTimeout(() => void poll(), pollIntervalMs)
      }

      server.httpServer?.once('listening', () => void poll())
      server.httpServer?.once('close', () => {
        stopped = true
        if (retryTimer !== undefined) clearTimeout(retryTimer)
      })
    },
  }
}
