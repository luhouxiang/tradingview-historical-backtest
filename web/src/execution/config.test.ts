import { describe, expect, it } from 'vitest'
import { capitalConfig, executionRequest } from './config'

describe('versioned execution configuration', () => {
  it('uses one real default set and includes the dataset multiplier for single-dataset work', () => {
    expect(executionRequest({ contractMultiplier: 20 })).toEqual({
      semantic_version: '1.0.0',
      signal_timing: 'bar_close',
      fill_timing: 'next_bar_open',
      commission: { mode: 'fixed_per_contract', amount_i64: 300, money_scale: 100 },
      slippage: { mode: 'ticks', value: 1 },
      contract_multiplier: 20,
      contract_multiplier_source: 'instrument_config',
      margin_ratio: 0.12,
      intrabar_conflict_rule: 'worst_case',
    })
    expect(capitalConfig()).toEqual({
      initial_cash_i64: 100_000_000,
      currency: 'CNY',
      money_scale: 100,
    })
  })

  it('leaves the multiplier to Go for a multi-dataset research request', () => {
    const execution = executionRequest()
    expect(execution.semantic_version).toBe('1.0.0')
    expect(execution).not.toHaveProperty('contract_multiplier')
    expect(execution).not.toHaveProperty('contract_multiplier_source')
  })
})
