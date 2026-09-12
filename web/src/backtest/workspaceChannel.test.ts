import { describe, expect, it } from 'vitest'
import type { DatasetMeta } from '../types/api'
import { createBacktestWorkspaceUrl } from './workspaceChannel'

describe('backtest workspace binding', () => {
  it('binds the detached page to an exact dataset revision', () => {
    const dataset = {
      dataset_id: 'SHFE.AOL9.5m', data_revision: `sha256:${'a'.repeat(64)}`,
    } as DatasetMeta
    const url = new URL(createBacktestWorkspaceUrl(dataset, 'http://127.0.0.1:5173'))
    expect(url.pathname).toBe('/backtest')
    expect(url.searchParams.get('dataset_id')).toBe(dataset.dataset_id)
    expect(url.searchParams.get('revision')).toBe(dataset.data_revision)
  })
})
