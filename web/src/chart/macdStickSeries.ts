import { customSeriesDefaultOptions } from 'lightweight-charts'
import type {
  CustomData,
  CustomSeriesOptions,
  ICustomSeriesPaneRenderer,
  ICustomSeriesPaneView,
  PaneRendererCustomData,
  PriceToCoordinateConverter,
  Time,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'
import { MARKET_COLORS } from './marketStyle'

export interface MacdStickData extends CustomData<Time> {
  value: number
  color: string
}

// WH6-style major marks do not label sub-5 fluctuations.
export function macdGuideStep(priceConverter: PriceToCoordinateConverter): number {
  const zero = priceConverter(0)
  if (zero === null) return 5
  let step = 5
  for (let index = 0; index < 24; index += 1) {
    const coordinate = priceConverter(step)
    if (coordinate === null || Math.abs(coordinate - zero) >= 20) break
    const magnitude = 10 ** Math.floor(Math.log10(step))
    const digit = step / magnitude
    step = (digit < 2 ? 2 : digit < 5 ? 5 : 10) * magnitude
  }
  return step
}

class MacdStickRenderer implements ICustomSeriesPaneRenderer {
  private data: PaneRendererCustomData<Time, MacdStickData> | null = null

  update(data: PaneRendererCustomData<Time, MacdStickData>): void {
    this.data = data
  }

  draw(target: CanvasRenderingTarget2D, priceConverter: PriceToCoordinateConverter): void {
    if (!this.data?.visibleRange) return
    const data = this.data
    target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio, verticalPixelRatio }) => {
      const zero = priceConverter(0)
      if (zero === null) return
      const step = macdGuideStep(priceConverter)
      context.save()
      context.strokeStyle = '#9f3030'
      context.fillStyle = '#aeb5c2'
      context.lineWidth = Math.max(1, horizontalPixelRatio)
      context.setLineDash([3 * horizontalPixelRatio, 3 * horizontalPixelRatio])
      const drawGuide = (value: number): void => {
        const coordinate = priceConverter(value)
        if (coordinate === null) return
        const y = Math.round(coordinate * verticalPixelRatio)
        const labelMargin = Math.round(9 * verticalPixelRatio)
        if (y < labelMargin || y > bitmapSize.height - labelMargin) return
        context.beginPath()
        context.moveTo(0, y + 0.5)
        context.lineTo(bitmapSize.width, y + 0.5)
        context.stroke()
      }
      drawGuide(0)
      for (let multiple = 1; multiple <= 20; multiple += 1) {
        const value = step * multiple
        const positive = priceConverter(value)
        const negative = priceConverter(-value)
        if ((positive === null || positive < 0) && (negative === null || negative > bitmapSize.height / verticalPixelRatio)) break
        drawGuide(value)
        drawGuide(-value)
      }
      context.restore()
      const from = Math.max(0, data.visibleRange!.from)
      const to = Math.min(data.bars.length, data.visibleRange!.to)
      const baseline = Math.round(zero * verticalPixelRatio)
      const width = Math.max(1, Math.floor(horizontalPixelRatio))

      for (let index = from; index < to; index += 1) {
        const bar = data.bars[index]
        if (!bar) continue
        const coordinate = priceConverter(bar.originalData.value)
        if (coordinate === null) continue
        const valueY = Math.round(coordinate * verticalPixelRatio)
        const top = Math.min(valueY, baseline)
        const height = Math.max(1, Math.abs(valueY - baseline))
        const center = Math.round(bar.x * horizontalPixelRatio)
        context.fillStyle = bar.barColor
        context.fillRect(center - Math.floor(width / 2), top, width, height)
      }
    })
  }
}

export class MacdStickSeries implements ICustomSeriesPaneView<Time, MacdStickData, CustomSeriesOptions> {
  private readonly paneRenderer = new MacdStickRenderer()

  renderer(): ICustomSeriesPaneRenderer {
    return this.paneRenderer
  }

  update(data: PaneRendererCustomData<Time, MacdStickData>): void {
    this.paneRenderer.update(data)
  }

  priceValueBuilder(data: MacdStickData): number[] {
    return [0, data.value, data.value]
  }

  isWhitespace(data: MacdStickData | CustomData<Time>): data is CustomData<Time> {
    return !('value' in data)
  }

  defaultOptions(): CustomSeriesOptions {
    return { ...customSeriesDefaultOptions, color: MARKET_COLORS.rising }
  }
}
