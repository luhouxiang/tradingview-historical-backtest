/** Calendar-date projection only; normalized UTC timestamps remain authoritative. */
export function localTrainingDate(timestamp: number, timezone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(timestamp)
  const get = (name: string) => parts.find(part => part.type === name)!.value
  return `${get('year')}-${get('month')}-${get('day')}`
}

export async function firstBarOnOrAfterDate(date: string, first: number, last: number, readDate: (index: number) => Promise<string>): Promise<number> {
  const parsed = new Date(`${date}T00:00:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10) !== date) throw new Error('请选择有效日期')
  let low = first, high = last, found: number | null = null
  while (low <= high) {
    const mid = low + Math.floor((high-low)/2)
    if (await readDate(mid) >= date) { found = mid; high = mid-1 } else low = mid+1
  }
  if (found === null) throw new Error('所选日期晚于历史行情末尾')
  return found
}
