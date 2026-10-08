export function barsPerSecond(position: number): number {
  const value = Math.max(0, Math.min(100, position))
  return value <= 50 ? Math.pow(60, (value - 50) / 50) : Math.pow(100, (value - 50) / 50)
}

export function speedLabel(position: number): string {
  const rate = barsPerSecond(position)
  if (rate < 1) return `${Number((1 / rate).toFixed(1))} 秒/根`
  return `${Number(rate.toFixed(1))} 根/秒`
}

/** Elapsed-time accounting retains fractional bars across rendered batches. */
export class SimulationClock {
  private previous = 0
  private remainder = 0
  start(now: number): void { this.previous = now; this.remainder = 0 }
  advance(now: number, rate: number): number {
    this.remainder += Math.max(0, now - this.previous) * rate / 1000
    this.previous = now
    const count = Math.floor(this.remainder + 1e-9)
    this.remainder -= count
    return count
  }
}
