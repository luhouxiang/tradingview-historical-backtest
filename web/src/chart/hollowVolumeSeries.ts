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

export interface HollowVolumeData extends CustomData<Time> {
  value: number
  rising: boolean
}

class HollowVolumeRenderer implements ICustomSeriesPaneRenderer {
  private data: PaneRendererCustomData<Time, HollowVolumeData> | null = null

  update(data: PaneRendererCustomData<Time, HollowVolumeData>): void {
    this.data = data
  }

  draw(target: CanvasRenderingTarget2D, priceConverter: PriceToCoordinateConverter): void {
    if (!this.data || !this.data.visibleRange) return
    const data = this.data
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
      const baseline = priceConverter(0)
      if (baseline === null) return
      const effectiveSpacing = data.barSpacing * data.conflationFactor
      const mediaWidth = Math.max(1, Math.min(effectiveSpacing * 0.8, effectiveSpacing - 1))
      const width = Math.max(1, Math.floor(mediaWidth * horizontalPixelRatio))
      const border = Math.max(1, Math.floor(Math.min(horizontalPixelRatio, verticalPixelRatio)))
      const from = Math.max(0, data.visibleRange!.from)
      const to = Math.min(data.bars.length, data.visibleRange!.to)
      const bottom = Math.round(baseline * verticalPixelRatio)

      for (let index = from; index < to; index += 1) {
        const bar = data.bars[index]
        if (!bar) continue
        const coordinate = priceConverter(bar.originalData.value)
        if (coordinate === null) continue
        const center = Math.round(bar.x * horizontalPixelRatio)
        const left = center - Math.floor(width / 2)
        const top = Math.round(coordinate * verticalPixelRatio)
        const height = Math.max(1, bottom - top)
        context.fillStyle = bar.originalData.rising ? MARKET_COLORS.rising : MARKET_COLORS.falling
        if (!bar.originalData.rising || width <= border * 2 || height <= border * 2) {
          context.fillRect(left, top, width, height)
          continue
        }
        context.fillRect(left, top, width, border)
        context.fillRect(left, bottom - border, width, border)
        context.fillRect(left, top + border, border, height - border * 2)
        context.fillRect(left + width - border, top + border, border, height - border * 2)
      }
    })
  }
}

export class HollowVolumeSeries implements ICustomSeriesPaneView<Time, HollowVolumeData, CustomSeriesOptions> {
  private readonly paneRenderer = new HollowVolumeRenderer()

  renderer(): ICustomSeriesPaneRenderer {
    return this.paneRenderer
  }

  update(data: PaneRendererCustomData<Time, HollowVolumeData>): void {
    this.paneRenderer.update(data)
  }

  priceValueBuilder(data: HollowVolumeData): number[] {
    return [0, data.value, data.value]
  }

  isWhitespace(data: HollowVolumeData | CustomData<Time>): data is CustomData<Time> {
    return !('value' in data)
  }

  defaultOptions(): CustomSeriesOptions {
    return { ...customSeriesDefaultOptions, color: MARKET_COLORS.falling }
  }
}
