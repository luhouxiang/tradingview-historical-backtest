import { describe, expect, it } from 'vitest'
import { isTradePointVisible } from './tradePointVisibility'

describe('trade-point layer visibility', () => {
  const visibility = { first_trade_points: false, second_trade_points: true, third_trade_points: false }

  it('routes standard and class-like signals independently, retaining legacy fallback', () => {
    expect(isTradePointVisible('buy_1', visibility)).toBe(false)
    expect(isTradePointVisible('class_sell_1', visibility)).toBe(false)
    expect(isTradePointVisible('buy_2', visibility)).toBe(true)
    expect(isTradePointVisible('class_sell_2', visibility)).toBe(true)
    expect(isTradePointVisible('sell_3', visibility)).toBe(false)
    const split = { ...visibility, class_first_trade_points: true, class_second_trade_points: false }
    expect(isTradePointVisible('buy_1', split)).toBe(false)
    expect(isTradePointVisible('class_buy_1', split)).toBe(true)
    expect(isTradePointVisible('buy_2', split)).toBe(true)
    expect(isTradePointVisible('class_sell_2', split)).toBe(false)
  })
})
