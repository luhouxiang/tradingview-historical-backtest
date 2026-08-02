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

class MacdStickRenderer implements ICustomSeriesPaneRenderer {
  private data: PaneRendererCustomData<Time, MacdStickData> | null = null

  update(data: PaneRendererCustomData<Time, MacdStickData>): void {
    this.data = data
  }

  draw(target: CanvasRenderingTarget2D, priceConverter: PriceToCoordinateConverter): void {
    if (!this.data?.visibleRange) return
    const data = this.data
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
      const zero = priceConverter(0)
      if (zero === null) return
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
