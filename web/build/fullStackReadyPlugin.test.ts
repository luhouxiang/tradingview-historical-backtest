import { EventEmitter } from 'node:events'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ViteDevServer } from 'vite'
import { createDemoDatasetReadyCheck, fullStackReadyPlugin } from './fullStackReadyPlugin'

function response(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response
}

afterEach(() => {
  vi.useRealTimers()
})

describe('fullStackReadyPlugin', () => {
  it('announces the chart only after the full stack is healthy', async () => {
    vi.useFakeTimers()
    const httpServer = new EventEmitter()
    const info = vi.fn()
    const checkReady = vi.fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true)
    const plugin = fullStackReadyPlugin({
      healthUrl: 'http://127.0.0.1:8080/api/v1/health',
      pageUrl: 'http://127.0.0.1:5173/',
      pollIntervalMs: 10,
      checkReady,
    })
    const server = {
      httpServer,
      config: { logger: { info } },
    } as unknown as ViteDevServer

    if (typeof plugin.configureServer !== 'function') throw new Error('configureServer hook is missing')
    const configureServer = plugin.configureServer as (server: ViteDevServer) => void
    configureServer(server)
    httpServer.emit('listening')
    await vi.advanceTimersByTimeAsync(0)

    expect(checkReady).toHaveBeenCalledTimes(1)
    expect(info).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(10)
    expect(checkReady).toHaveBeenCalledTimes(2)
    expect(info).toHaveBeenCalledOnce()
    expect(info).toHaveBeenCalledWith('TVBT full stack ready: http://127.0.0.1:5173/')
  })

  it('stops polling when the Vite server closes', async () => {
    vi.useFakeTimers()
    const httpServer = new EventEmitter()
    const checkReady = vi.fn().mockResolvedValue(false)
    const plugin = fullStackReadyPlugin({
      healthUrl: 'http://127.0.0.1:8080/api/v1/health',
      pageUrl: 'http://127.0.0.1:5173/',
      pollIntervalMs: 10,
      checkReady,
    })
    const server = {
      httpServer,
      config: { logger: { info: vi.fn() } },
    } as unknown as ViteDevServer

    if (typeof plugin.configureServer !== 'function') throw new Error('configureServer hook is missing')
    const configureServer = plugin.configureServer as (server: ViteDevServer) => void
    configureServer(server)
    httpServer.emit('listening')
    await vi.advanceTimersByTimeAsync(0)
    httpServer.emit('close')
    await vi.advanceTimersByTimeAsync(100)

    expect(checkReady).toHaveBeenCalledOnce()
  })
})

describe('createDemoDatasetReadyCheck', () => {
  it('leaves an existing preferred demo dataset unchanged', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ datasets: [{ dataset_id: 'SHFE.AOL9.5m' }] }))
    const checkReady = createDemoDatasetReadyCheck({
      apiBaseUrl: 'http://127.0.0.1:8080',
      fetchImpl,
    })

    await expect(checkReady()).resolves.toBe(true)
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(fetchImpl).not.toHaveBeenCalledWith(expect.stringContaining('/datasets/scan'), expect.anything())
  })

  it('scans and imports only the prepared AOL9 sample when the preferred dataset is absent', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
      .mockResolvedValueOnce(response({ job_id: 'scan-1' }))
      .mockResolvedValueOnce(response({ job_id: 'scan-1', status: 'completed' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
      .mockResolvedValueOnce(response({
        items: [{
          source_file_id: 'source-1',
          status: 'importable',
          detected: {
            exchange: 'SHFE',
            symbol: 'AOL9',
            timeframe: '5m',
            date_semantics: 'trading_day',
            timezone: 'Asia/Shanghai',
            timestamp_semantics: 'bar_end',
          },
        }],
      }))
      .mockResolvedValueOnce(response({ job_id: 'import-1' }))
      .mockResolvedValueOnce(response({ job_id: 'import-1', status: 'completed' }))
      .mockResolvedValueOnce(response({ datasets: [{ dataset_id: 'SHFE.AOL9.5m' }] }))
    const checkReady = createDemoDatasetReadyCheck({
      apiBaseUrl: 'http://127.0.0.1:8080',
      fetchImpl,
      jobPollIntervalMs: 1,
    })

    await expect(checkReady()).resolves.toBe(true)
    const importCall = fetchImpl.mock.calls.find(([url]) => String(url).endsWith('/api/v1/datasets/import'))
    expect(importCall).toBeDefined()
    expect(JSON.parse(String(importCall?.[1]?.body))).toMatchObject({
      source_file_id: 'source-1',
      instrument: 'AOL9',
      importer_id: 'tdx_txt_v1',
    })
  })

  it('uses the configured preferred symbol when bootstrapping the dataset', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
      .mockResolvedValueOnce(response({ job_id: 'scan-1' }))
      .mockResolvedValueOnce(response({ job_id: 'scan-1', status: 'completed' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
      .mockResolvedValueOnce(response({
        items: [{
          source_file_id: 'source-ao2609',
          status: 'importable',
          detected: {
            exchange: 'SHFE',
            symbol: 'AO2609',
            timeframe: '5m',
          },
        }],
      }))
      .mockResolvedValueOnce(response({ job_id: 'import-1' }))
      .mockResolvedValueOnce(response({ job_id: 'import-1', status: 'completed' }))
      .mockResolvedValueOnce(response({ datasets: [{ dataset_id: 'SHFE.AO2609.5m' }] }))
    const checkReady = createDemoDatasetReadyCheck({
      apiBaseUrl: 'http://127.0.0.1:8080',
      preferredSymbol: 'AO2609',
      fetchImpl,
      jobPollIntervalMs: 1,
    })

    await expect(checkReady()).resolves.toBe(true)
    const importCall = fetchImpl.mock.calls.find(([url]) => String(url).endsWith('/api/v1/datasets/import'))
    expect(JSON.parse(String(importCall?.[1]?.body))).toMatchObject({ instrument: 'AO2609' })
  })

  it('does not submit repeated scan jobs after a terminal bootstrap failure', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
      .mockResolvedValueOnce(response({ job_id: 'scan-failed' }))
      .mockResolvedValueOnce(response({
        job_id: 'scan-failed',
        status: 'failed',
        error: { message: 'scan failed' },
      }))
      .mockResolvedValueOnce(response({ status: 'ok' }))
      .mockResolvedValueOnce(response({ datasets: [] }))
    const checkReady = createDemoDatasetReadyCheck({
      apiBaseUrl: 'http://127.0.0.1:8080',
      fetchImpl,
      jobPollIntervalMs: 1,
    })

    await expect(checkReady()).resolves.toBe(false)
    await expect(checkReady()).resolves.toBe(false)
    const scanCalls = fetchImpl.mock.calls.filter(([url]) => String(url).endsWith('/api/v1/datasets/scan'))
    expect(scanCalls).toHaveLength(1)
  })
})
