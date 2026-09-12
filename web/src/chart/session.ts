import { getBars } from '../api/client'
import type { BarRangeResponse, DatasetMeta } from '../types/api'

export interface CachedBar {
  barIndex: number
  timestampUtc: number
  openI64: number
  highI64: number
  lowI64: number
  closeI64: number
  volume: number
  openInterest: number | null
}

export type BarFetcher = typeof getBars

function generationId(): string {
  return `gen-${crypto.randomUUID().replaceAll('-', '')}`
}

export class ChartSession {
  private readonly byIndex = new Map<number, CachedBar>()
  private inflightBefore: Promise<number> | null = null
  private inflightAfter: Promise<number> | null = null
  private hasMoreBeforeValue = false
  private hasMoreAfterValue = false
  private metaValue: DatasetMeta | null = null
  private generationValue = ''
  private rangeEpoch = 0

  constructor(private readonly fetchBars: BarFetcher = getBars) {}

  get meta(): DatasetMeta | null {
    return this.metaValue
  }

  get generation(): string {
    return this.generationValue
  }

  get hasMoreBefore(): boolean {
    return this.hasMoreBeforeValue
  }

  get hasMoreAfter(): boolean {
    return this.hasMoreAfterValue
  }

  get bars(): CachedBar[] {
    return [...this.byIndex.values()].sort((left, right) => left.barIndex - right.barIndex)
  }

  async open(meta: DatasetMeta): Promise<CachedBar[]> {
    this.rangeEpoch += 1
    this.metaValue = meta
    this.generationValue = generationId()
    this.byIndex.clear()
    this.hasMoreBeforeValue = false
    this.hasMoreAfterValue = false
    this.inflightBefore = null
    this.inflightAfter = null
    const generation = this.generationValue
    const response = await this.fetchBars(meta.dataset_id, meta.data_revision, generation, { tail: 3000 })
    if (generation !== this.generationValue) return this.bars
    this.merge(response)
    return this.bars
  }

  prefetchBefore(): Promise<number> {
    if (!this.metaValue || !this.hasMoreBeforeValue || this.byIndex.size === 0) return Promise.resolve(0)
    if (this.inflightBefore) return this.inflightBefore
    const meta = this.metaValue
    const generation = this.generationValue
    const rangeEpoch = this.rangeEpoch
    const beforeBarIndex = Math.min(...this.byIndex.keys())
    this.inflightBefore = this.fetchBars(meta.dataset_id, meta.data_revision, generation, {
      beforeBarIndex,
      limit: 1500,
    })
      .then((response) => {
        if (generation !== this.generationValue || rangeEpoch !== this.rangeEpoch) return 0
        const before = this.byIndex.size
        this.merge(response)
        return this.byIndex.size - before
      })
      .finally(() => {
        if (generation === this.generationValue && rangeEpoch === this.rangeEpoch) this.inflightBefore = null
      })
    return this.inflightBefore
  }

  prefetchAfter(): Promise<number> {
    if (!this.metaValue || !this.hasMoreAfterValue || this.byIndex.size === 0) return Promise.resolve(0)
    if (this.inflightAfter) return this.inflightAfter
    const meta = this.metaValue
    const generation = this.generationValue
    const rangeEpoch = this.rangeEpoch
    const afterBarIndex = Math.max(...this.byIndex.keys())
    this.inflightAfter = this.fetchBars(meta.dataset_id, meta.data_revision, generation, {
      afterBarIndex,
      limit: 1500,
    })
      .then((response) => {
        if (generation !== this.generationValue || rangeEpoch !== this.rangeEpoch) return 0
        const before = this.byIndex.size
        this.merge(response)
        return this.byIndex.size - before
      })
      .finally(() => {
        if (generation === this.generationValue && rangeEpoch === this.rangeEpoch) this.inflightAfter = null
      })
    return this.inflightAfter
  }

  async loadAround(barIndex: number, radius = 120, replace = false): Promise<number> {
    if (!this.metaValue) return 0
    const meta = this.metaValue
    const generation = this.generationValue
    if (replace) {
      this.rangeEpoch += 1
      this.inflightBefore = null
      this.inflightAfter = null
    }
    const rangeEpoch = this.rangeEpoch
    const first = Math.max(meta.coverage.first_bar_index, barIndex - radius)
    const last = Math.min(meta.coverage.last_bar_index, barIndex + radius)
    const beforeBarIndex = last + 1
    const before = replace ? 0 : this.byIndex.size
    const response = await this.fetchBars(meta.dataset_id, meta.data_revision, generation, {
      beforeBarIndex,
      limit: last - first + 1,
    })
    if (generation !== this.generationValue || rangeEpoch !== this.rangeEpoch) return 0
    if (replace) this.byIndex.clear()
    this.merge(response)
    return this.byIndex.size - before
  }

  private merge(response: BarRangeResponse): void {
    const meta = this.metaValue
    if (
      !meta ||
      response.dataset_id !== meta.dataset_id ||
      response.data_revision !== meta.data_revision ||
      response.generation_id !== this.generationValue ||
      !/^sha256:[0-9a-f]{64}$/.test(response.checksum)
    ) {
      throw new Error('K-line response identity does not match the active chart session')
    }
    const bars = response.bars
    const length = bars.bar_index.length
    const columns = [
      bars.timestamp_utc,
      bars.open_i64,
      bars.high_i64,
      bars.low_i64,
      bars.close_i64,
      bars.volume,
      bars.open_interest,
    ]
    if (columns.some((column) => column.length !== length)) {
      throw new Error('K-line response columns have different lengths')
    }
    for (let index = 0; index < length; index += 1) {
      const barIndex = bars.bar_index[index]
      this.byIndex.set(barIndex, {
        barIndex,
        timestampUtc: bars.timestamp_utc[index],
        openI64: bars.open_i64[index],
        highI64: bars.high_i64[index],
        lowI64: bars.low_i64[index],
        closeI64: bars.close_i64[index],
        volume: bars.volume[index],
        openInterest: bars.open_interest[index],
      })
    }
    if (this.byIndex.size === 0) {
      this.hasMoreBeforeValue = false
      this.hasMoreAfterValue = false
      return
    }
    this.hasMoreBeforeValue = Math.min(...this.byIndex.keys()) > meta.coverage.first_bar_index
    this.hasMoreAfterValue = Math.max(...this.byIndex.keys()) < meta.coverage.last_bar_index
  }
}
