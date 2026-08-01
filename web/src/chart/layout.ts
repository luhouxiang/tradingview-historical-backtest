export interface PaneLayout {
  id: string
  kind: 'price' | 'indicator'
  weight: number
  minHeight: number
}

export function defaultPaneLayout(): PaneLayout[] {
  return [
    { id: 'price', kind: 'price', weight: 6, minHeight: 240 },
    { id: 'macd', kind: 'indicator', weight: 1, minHeight: 80 },
    { id: 'volume', kind: 'indicator', weight: 1, minHeight: 80 },
  ]
}

export function resizeAdjacent(panes: PaneLayout[], index: number, deltaPixels: number, totalHeight: number): PaneLayout[] {
  if (index < 0 || index >= panes.length - 1 || totalHeight <= 0) return panes.map((pane) => ({ ...pane }))
  const totalWeight = panes.reduce((sum, pane) => sum + pane.weight, 0)
  const upper = panes[index]
  const lower = panes[index + 1]
  const upperPixels = (upper.weight / totalWeight) * totalHeight
  const lowerPixels = (lower.weight / totalWeight) * totalHeight
  const clampedDelta = Math.max(upper.minHeight - upperPixels, Math.min(deltaPixels, lowerPixels - lower.minHeight))
  const next = panes.map((pane) => ({ ...pane }))
  next[index].weight = ((upperPixels + clampedDelta) / totalHeight) * totalWeight
  next[index + 1].weight = ((lowerPixels - clampedDelta) / totalHeight) * totalWeight
  return next
}

export function enforceMinimumHeights(panes: PaneLayout[], totalHeight: number): PaneLayout[] {
  if (panes.length === 0 || totalHeight <= 0) return panes.map((pane) => ({ ...pane }))
  const minimumTotal = panes.reduce((sum, pane) => sum + pane.minHeight, 0)
  const available = Math.max(totalHeight, minimumTotal)
  const heights = new Map<string, number>()
  let unresolved = panes.map((pane) => ({ ...pane }))
  let remainingHeight = available
  for (;;) {
    const remainingWeight = unresolved.reduce((sum, pane) => sum + pane.weight, 0)
    const constrained = unresolved.filter((pane) => (pane.weight / remainingWeight) * remainingHeight < pane.minHeight)
    if (constrained.length === 0) {
      unresolved.forEach((pane) => heights.set(pane.id, (pane.weight / remainingWeight) * remainingHeight))
      break
    }
    constrained.forEach((pane) => {
      heights.set(pane.id, pane.minHeight)
      remainingHeight -= pane.minHeight
    })
    const ids = new Set(constrained.map((pane) => pane.id))
    unresolved = unresolved.filter((pane) => !ids.has(pane.id))
    if (unresolved.length === 0) break
  }
  return panes.map((pane) => ({ ...pane, weight: heights.get(pane.id) ?? pane.minHeight }))
}

export function removePane(panes: PaneLayout[], paneId: string): PaneLayout[] {
  const pane = panes.find((candidate) => candidate.id === paneId)
  if (!pane || pane.kind === 'price') return panes.map((candidate) => ({ ...candidate }))
  return panes.filter((candidate) => candidate.id !== paneId).map((candidate) => ({ ...candidate }))
}
