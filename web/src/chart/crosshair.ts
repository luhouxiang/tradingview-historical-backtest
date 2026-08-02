import type { CachedBar } from './session'

const WEEKDAYS: Record<string, string> = {
  Sun: '日', Mon: '一', Tue: '二', Wed: '三', Thu: '四', Fri: '五', Sat: '六',
}

export function barAtLogicalIndex(bars: CachedBar[], logical: number | null | undefined): CachedBar | null {
  if (logical === null || logical === undefined || !Number.isFinite(logical)) return null
  return bars[Math.round(logical)] ?? null
}

function timeframeMilliseconds(timeframe: string): number {
  const match = /^(\d+)([smhdw])$/.exec(timeframe)
  if (!match) return 0
  const value = Number(match[1])
  const unit = { s: 1000, m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000 }[match[2]!]
  return value * (unit ?? 0)
}

function dateParts(timestamp: number, timezone: string): Record<string, string> {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short',
  }).formatToParts(new Date(timestamp))
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

export function formatBarTimeRange(
  timestampUtc: number,
  timeframe: string,
  timezone: string,
  timestampSemantics: 'bar_start' | 'bar_end' = 'bar_end',
): string {
  const duration = timeframeMilliseconds(timeframe)
  const startTimestamp = timestampSemantics === 'bar_start' ? timestampUtc : timestampUtc - duration
  const endTimestamp = timestampSemantics === 'bar_start' ? timestampUtc + duration : timestampUtc
  const start = dateParts(startTimestamp, timezone)
  const end = dateParts(endTimestamp, timezone)
  const startDate = `${start.year}/${start.month}/${start.day}`
  const endDate = `${end.year}/${end.month}/${end.day}`
  const endText = startDate === endDate
    ? `${end.hour}:${end.minute}`
    : `${endDate} ${end.hour}:${end.minute}`
  return `${startDate} ${start.hour}:${start.minute}~${endText} ${WEEKDAYS[start.weekday!] ?? start.weekday ?? ''}`.trim()
}
