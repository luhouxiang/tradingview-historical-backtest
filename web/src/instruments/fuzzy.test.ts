import { describe, expect, it } from 'vitest'
import { fuzzyInstruments, type InstrumentSearchItem } from './fuzzy'

const items: InstrumentSearchItem[] = [
  { id: '1', symbol: 'AO2609', timeframe: '5m', path: 'history/30#AO2609.txt', label: '氧化铝2609', status: 'imported' },
  { id: '2', symbol: 'AOL9', timeframe: '5m', path: 'history/30#AOL9.txt', label: '氧化铝加权', status: 'imported' },
  { id: '3', symbol: 'RB2610', timeframe: '5m', path: 'history/30#RB2610.txt', label: '螺纹钢2610', status: 'importable' },
]

describe('fuzzyInstruments', () => {
  it('prioritizes exact and prefix symbol matches', () => {
    expect(fuzzyInstruments(items, 'aol').map((item) => item.symbol)).toEqual(['AOL9'])
    expect(fuzzyInstruments(items, 'ao').map((item) => item.symbol)).toEqual(['AOL9', 'AO2609'])
  })

  it('matches display labels, paths and ordered subsequences case-insensitively', () => {
    expect(fuzzyInstruments(items, '氧化铝').map((item) => item.symbol)).toEqual(['AO2609', 'AOL9'])
    expect(fuzzyInstruments(items, 'rb10').map((item) => item.symbol)).toEqual(['RB2610'])
  })
})
