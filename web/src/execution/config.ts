import type { BacktestRequest } from '../types/api'

export const EXECUTION_SEMANTIC_VERSION = '1.0.0' as const
export const MONEY_SCALE = 100

export function executionRequest(options: {
  commissionAmountI64?: number
  slippageTicks?: number
  marginRatio?: number
  contractMultiplier?: number
} = {}): BacktestRequest['execution'] {
  return {
    semantic_version: EXECUTION_SEMANTIC_VERSION,
    signal_timing: 'bar_close',
    fill_timing: 'next_bar_open',
    commission: {
      mode: 'fixed_per_contract',
      amount_i64: options.commissionAmountI64 ?? 300,
      money_scale: MONEY_SCALE,
    },
    slippage: { mode: 'ticks', value: options.slippageTicks ?? 1 },
    ...(options.contractMultiplier === undefined
      ? {}
      : {
          contract_multiplier: options.contractMultiplier,
          contract_multiplier_source: 'instrument_config' as const,
        }),
    margin_ratio: options.marginRatio ?? 0.12,
    intrabar_conflict_rule: 'worst_case',
  }
}

export function capitalConfig(initialCashI64 = 100_000_000): BacktestRequest['capital'] {
  return { initial_cash_i64: initialCashI64, currency: 'CNY', money_scale: MONEY_SCALE }
}
