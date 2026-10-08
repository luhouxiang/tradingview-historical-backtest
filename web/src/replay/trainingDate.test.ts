import { describe, expect, it } from 'vitest'
import { firstBarOnOrAfterDate, localTrainingDate } from './trainingDate'

describe('training calendar date', () => {
  it('uses dataset timezone rather than browser timezone or trading-day subtraction', () => {
    expect(localTrainingDate(Date.UTC(2026,7,7,17), 'Asia/Shanghai')).toBe('2026-08-08')
    expect(localTrainingDate(Date.UTC(2026,7,7,17), 'UTC')).toBe('2026-08-07')
  })
  it('chooses first available bar at or after a date, including holidays', async () => {
    const dates = ['2026-08-07','2026-08-07','2026-08-10','2026-08-10','2026-08-11']
    const read = async (i: number) => dates[i-100]!
    expect(await firstBarOnOrAfterDate('2026-08-07',100,104,read)).toBe(100)
    expect(await firstBarOnOrAfterDate('2026-08-08',100,104,read)).toBe(102)
    expect(await firstBarOnOrAfterDate('2026-08-10',100,104,read)).toBe(102)
    await expect(firstBarOnOrAfterDate('2026-08-12',100,104,read)).rejects.toThrow('末尾')
    await expect(firstBarOnOrAfterDate('2026-02-30',100,104,read)).rejects.toThrow('有效日期')
  })
})
