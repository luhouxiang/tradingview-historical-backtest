export type LogLevel = 'TRACE' | 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'

export interface SourceLocation {
  source_file: string
  source_line: number
  source_function: string
}

export interface ClientLogEvent extends SourceLocation {
  timestamp: string
  level: LogLevel
  service: 'vue-client'
  event: string
  message: string
  fields?: Record<string, unknown>
}

type Sender = (events: ClientLogEvent[], beacon: boolean) => Promise<boolean>

const FALLBACK_SOURCE: SourceLocation = {
  source_file: 'unknown.ts',
  source_line: 1,
  source_function: 'unknown',
}

async function defaultSender(events: ClientLogEvent[], beacon: boolean): Promise<boolean> {
  const body = JSON.stringify({ events })
  if (beacon && navigator.sendBeacon !== undefined) {
    return navigator.sendBeacon('/api/v1/client-logs', new Blob([body], { type: 'application/json' }))
  }
  try {
    const response = await fetch('/api/v1/client-logs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    })
    return response.ok
  } catch {
    return false
  }
}

function isSource(value: unknown): value is SourceLocation {
  return typeof value === 'object' && value !== null && 'source_file' in value && 'source_line' in value
}

export class ClientLogger {
  private queue: ClientLogEvent[] = []
  private droppedCount = 0
  private timer: number | undefined
  private flushing = false

  constructor(
    private readonly sender: Sender = defaultSender,
    private readonly maxQueue = 1000,
  ) {}

  start(): void {
    if (this.timer !== undefined) return
    this.timer = window.setInterval(() => void this.flush(), 1000)
    window.addEventListener('pagehide', this.onPageHide)
    document.addEventListener('visibilitychange', this.onVisibilityChange)
  }

  stop(): void {
    if (this.timer !== undefined) window.clearInterval(this.timer)
    this.timer = undefined
    window.removeEventListener('pagehide', this.onPageHide)
    document.removeEventListener('visibilitychange', this.onVisibilityChange)
  }

  trace(event: string, message: string, fields?: Record<string, unknown>): void
  trace(source: SourceLocation, event: string, message: string, fields?: Record<string, unknown>): void
  trace(...args: unknown[]): void { this.record('TRACE', args) }

  debug(event: string, message: string, fields?: Record<string, unknown>): void
  debug(source: SourceLocation, event: string, message: string, fields?: Record<string, unknown>): void
  debug(...args: unknown[]): void { this.record('DEBUG', args) }

  info(event: string, message: string, fields?: Record<string, unknown>): void
  info(source: SourceLocation, event: string, message: string, fields?: Record<string, unknown>): void
  info(...args: unknown[]): void { this.record('INFO', args) }

  warn(event: string, message: string, fields?: Record<string, unknown>): void
  warn(source: SourceLocation, event: string, message: string, fields?: Record<string, unknown>): void
  warn(...args: unknown[]): void { this.record('WARN', args) }

  error(event: string, message: string, fields?: Record<string, unknown>): void
  error(source: SourceLocation, event: string, message: string, fields?: Record<string, unknown>): void
  error(...args: unknown[]): void { this.record('ERROR', args) }

  async flush(beacon = false): Promise<void> {
    if (this.flushing || this.queue.length === 0) return
    this.flushing = true
    const batch = this.queue.splice(0, 100)
    const sent = await this.sender(batch, beacon)
    if (!sent && !beacon) {
      this.queue.unshift(...batch)
      this.queue.splice(this.maxQueue)
    }
    this.flushing = false
  }

  pending(): number {
    return this.queue.length
  }

  private record(level: LogLevel, args: unknown[]): void {
    const source = isSource(args[0]) ? args.shift() as SourceLocation : FALLBACK_SOURCE
    const [event, message, fields] = args as [string, string, Record<string, unknown> | undefined]
    if (this.queue.length >= this.maxQueue) {
      if (level === 'TRACE' || level === 'DEBUG') {
        this.droppedCount++
        return
      }
      this.queue.shift()
      this.droppedCount++
    }
    if (this.droppedCount > 0) {
      this.queue.push({
        ...source,
        timestamp: new Date().toISOString(),
        level: 'WARN',
        service: 'vue-client',
        event: 'logging.events_dropped',
        message: 'Client log queue dropped low-priority events',
        fields: { dropped_count: this.droppedCount },
      })
      this.droppedCount = 0
    }
    this.queue.push({
      ...source,
      timestamp: new Date().toISOString(),
      level,
      service: 'vue-client',
      event,
      message,
      ...(fields === undefined ? {} : { fields }),
    })
    if (this.queue.length >= 100) void this.flush()
  }

  private readonly onPageHide = (): void => { void this.flush(true) }
  private readonly onVisibilityChange = (): void => {
    if (document.visibilityState === 'hidden') void this.flush(true)
  }
}

export const logger = new ClientLogger()

