import type { BacktestTrade, DatasetMeta, StrategyRunSource } from '../types/api'

export const BACKTEST_WORKSPACE_CHANNEL = 'tvbt:backtest-workspace:v1'

export type BacktestWorkspaceMessage =
  | {
    type: 'run-completed'
    dataset_id: string
    data_revision: string
    source: StrategyRunSource
  }
  | {
    type: 'focus-trade'
    dataset_id: string
    data_revision: string
    trade: BacktestTrade
  }

export function createBacktestWorkspaceUrl(dataset: DatasetMeta, origin: string): string {
  const url = new URL('/backtest', origin)
  url.searchParams.set('dataset_id', dataset.dataset_id)
  url.searchParams.set('revision', dataset.data_revision)
  return url.toString()
}

export function createBacktestWorkspaceChannel(): BroadcastChannel | null {
  return typeof BroadcastChannel === 'undefined'
    ? null
    : new BroadcastChannel(BACKTEST_WORKSPACE_CHANNEL)
}
