import { getCalculationResults } from '../api/client'
import type { CalculationResults, ChanCalculationResults, IndicatorCalculationResults } from '../types/api'

// The Go Results API accepts at most 5000 inclusive bar indices per request.
export const CALCULATION_PAGE_SIZE = 5000
export type CalculationWindow =
  | Pick<IndicatorCalculationResults, 'result_kind' | 'bar_index' | 'values'>
  | Pick<ChanCalculationResults, 'result_kind' | 'objects'>

function sameIdentity(left: CalculationResults, right: CalculationResults): boolean {
  return left.result_kind === right.result_kind
    && left.job_id === right.job_id && left.cache_key === right.cache_key
    && left.dataset_id === right.dataset_id && left.data_revision === right.data_revision
    && left.algorithm?.kind === right.algorithm?.kind
    && left.algorithm?.algorithm_id === right.algorithm?.algorithm_id
    && left.algorithm?.algorithm_version === right.algorithm?.algorithm_version
    && left.algorithm?.source_hash === right.algorithm?.source_hash
}

/** Fetch an entire viewport from immutable results, not a new calculation.
 * Cross-page objects may straddle multiple pages; preserve one object per ID.
 * This is a render projection, deliberately not an API response with a fake checksum.
 */
export async function loadCalculationWindow(
  jobId: string,
  from: number,
  to: number,
  isCurrent: () => boolean = () => true,
  fetchPage: typeof getCalculationResults = getCalculationResults,
): Promise<CalculationWindow | null> {
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from < 0 || to < from) {
    throw new Error('指标结果查询范围无效')
  }
  const pages: CalculationResults[] = []
  // Serial pages bound memory/network pressure and stop obsolete requests early.
  for (let start = from; start <= to; start += CALCULATION_PAGE_SIZE) {
    if (!isCurrent()) return null
    const page = await fetchPage(jobId, start, Math.min(to, start + CALCULATION_PAGE_SIZE - 1))
    if (!isCurrent()) return null
    if (pages[0] && !sameIdentity(pages[0], page)) throw new Error('指标结果分页的数据身份不一致')
    pages.push(page)
  }
  const first = pages[0]!
  if (first.result_kind === 'indicator') {
    const rows = new Map<number, Record<string, number | null>>()
    const names = new Set<string>()
    for (const page of pages) {
      if (page.result_kind !== 'indicator') throw new Error('指标结果分页类型不一致')
      for (const [name, values] of Object.entries(page.values)) {
        names.add(name)
        if (values.length !== page.bar_index.length) throw new Error('指标结果列长度不一致')
      }
      page.bar_index.forEach((index, offset) => {
        const values = Object.fromEntries(Object.entries(page.values).map(([name, column]) => [name, column[offset]!]))
        rows.set(index, values)
      })
    }
    const indices = [...rows.keys()].sort((a, b) => a - b)
    return {
      result_kind: 'indicator', bar_index: indices,
      values: Object.fromEntries([...names].map((name) => [name, indices.map((index) => rows.get(index)![name] ?? null)])),
    }
  }
  const objects = {} as ChanCalculationResults['objects']
  for (const category of Object.keys(first.objects) as Array<keyof typeof objects>) {
    const byId = new Map<string, { object_id: string; object_revision: number }>()
    for (const page of pages) {
      if (page.result_kind !== 'chan') throw new Error('缠论结果分页类型不一致')
      for (const item of page.objects[category] ?? []) {
        const previous = byId.get(item.object_id)
        if (!previous || item.object_revision >= previous.object_revision) byId.set(item.object_id, item)
      }
    }
    // Each category remains homogeneous; the type assertion only bridges the mapped union.
    Object.assign(objects, { [category]: [...byId.values()] })
  }
  return { result_kind: 'chan', objects }
}
