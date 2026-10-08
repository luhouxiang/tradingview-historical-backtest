import { describe, expect, it } from 'vitest'
import { barsPerSecond, SimulationClock, speedLabel } from './simulationSpeed'

describe('simulation speed', () => {
  it('has exact requested endpoints and midpoint', () => {
    expect(barsPerSecond(0)).toBeCloseTo(1 / 60)
    expect(barsPerSecond(50)).toBe(1)
    expect(barsPerSecond(100)).toBe(100)
    expect(speedLabel(0)).toBe('60 秒/根')
    expect(speedLabel(50)).toBe('1 根/秒')
    expect(speedLabel(100)).toBe('100 根/秒')
  })
  it('advances by elapsed time, preserving fractional bars', () => {
    const clock = new SimulationClock()
    clock.start(0)
    for (let i = 1; i < 20; i++) expect(clock.advance(i * 50, 1)).toBe(0)
    expect(clock.advance(1000, 1)).toBe(1)
    expect(clock.advance(1050, 100)).toBe(5)
    clock.start(0)
    expect(clock.advance(59999, 1 / 60)).toBe(0)
    expect(clock.advance(60000, 1 / 60)).toBe(1)
    expect(clock.advance(66000, 100)).toBe(600)
  })
})
