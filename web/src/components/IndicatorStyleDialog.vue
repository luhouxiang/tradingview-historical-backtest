<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  colorWithOpacity,
  completeIndicatorStyle,
  indicatorPalette,
  styleableOutputs,
  type StyleSource,
} from '../indicators/style'
import type { IndicatorLineStyleName, IndicatorOutputStyle, IndicatorStyle } from '../types/api'

const props = defineProps<{ source: StyleSource }>()
const emit = defineEmits<{
  confirm: [style: IndicatorStyle]
  cancel: []
}>()

const outputs = computed(() => styleableOutputs(props.source))
const draft = ref<IndicatorStyle>(completeIndicatorStyle(props.source))
const editingOutput = ref<string | null>(null)
const dialogElement = ref<HTMLElement | null>(null)
const lineStyles: Array<{ value: IndicatorLineStyleName; label: string }> = [
  { value: 'solid', label: '实线' },
  { value: 'dashed', label: '虚线' },
  { value: 'dotted', label: '点线' },
]

function outputStyle(name: string): IndicatorOutputStyle {
  return draft.value.outputs[name]!
}

function patchOutput(name: string, patch: Partial<IndicatorOutputStyle>): void {
  draft.value = {
    outputs: {
      ...draft.value.outputs,
      [name]: { ...outputStyle(name), ...patch },
    },
  }
}

function previewStyle(style: IndicatorOutputStyle): Record<string, string> {
  return {
    borderTopColor: colorWithOpacity(style.color, style.opacity),
    borderTopWidth: `${style.line_width}px`,
    borderTopStyle: style.line_style === 'solid' ? 'solid' : style.line_style,
  }
}

function confirm(): void {
  emit('confirm', {
    outputs: Object.fromEntries(Object.entries(draft.value.outputs).map(([name, style]) => [name, { ...style }])),
  })
}

onMounted(() => nextTick(() => dialogElement.value?.focus()))
</script>

<template>
  <div class="indicator-style-backdrop" @mousedown.self="emit('cancel')">
    <section ref="dialogElement" class="indicator-style-dialog" role="dialog" aria-modal="true" tabindex="-1" :aria-label="`${source.definition.name} 样式`" @keydown.esc="emit('cancel')">
      <header>
        <strong>样式</strong>
        <button type="button" aria-label="关闭样式设置" @click="emit('cancel')">×</button>
      </header>
      <div class="indicator-style-tab">样式</div>

      <div class="indicator-style-rows">
        <div v-for="output in outputs" :key="output.name" class="indicator-style-row">
          <label>
            <input
              type="checkbox"
              :checked="outputStyle(output.name).visible"
              :aria-label="`显示 ${output.display_name}`"
              @change="patchOutput(output.name, { visible: ($event.target as HTMLInputElement).checked })"
            />
            <span>{{ output.display_name }}</span>
          </label>
          <button
            type="button"
            class="indicator-style-preview"
            :aria-label="`设置 ${output.display_name} 样式`"
            :aria-expanded="editingOutput === output.name"
            @click="editingOutput = editingOutput === output.name ? null : output.name"
          >
            <i :style="{ backgroundColor: outputStyle(output.name).color }" />
            <span :style="previewStyle(outputStyle(output.name))" />
          </button>

          <div v-if="editingOutput === output.name" class="indicator-color-popover">
            <div class="indicator-color-custom">
              <input
                type="color"
                :value="outputStyle(output.name).color"
                :aria-label="`${output.display_name} 自定义颜色`"
                @input="patchOutput(output.name, { color: ($event.target as HTMLInputElement).value })"
              />
              <code>{{ outputStyle(output.name).color.toUpperCase() }}</code>
            </div>
            <div class="indicator-color-grid" aria-label="颜色">
              <button
                v-for="color in indicatorPalette"
                :key="color"
                type="button"
                :class="{ selected: outputStyle(output.name).color.toLowerCase() === color }"
                :style="{ backgroundColor: color }"
                :aria-label="`选择颜色 ${color}`"
                @click="patchOutput(output.name, { color })"
              />
            </div>

            <label class="indicator-opacity">
              <span>不透明度</span>
              <input
                type="range"
                min="10"
                max="100"
                step="1"
                :value="Math.round(outputStyle(output.name).opacity * 100)"
                :aria-label="`${output.display_name} 不透明度`"
                @input="patchOutput(output.name, { opacity: Number(($event.target as HTMLInputElement).value) / 100 })"
              />
              <output>{{ Math.round(outputStyle(output.name).opacity * 100) }}%</output>
            </label>

            <fieldset>
              <legend>厚度</legend>
              <div class="indicator-width-options">
                <button
                  v-for="width in ([1, 2, 3, 4] as const)"
                  :key="width"
                  type="button"
                  :class="{ selected: outputStyle(output.name).line_width === width }"
                  :aria-label="`线宽 ${width}`"
                  @click="patchOutput(output.name, { line_width: width })"
                ><span :style="{ borderTopWidth: `${width}px` }" /></button>
              </div>
            </fieldset>

            <fieldset>
              <legend>线条样式</legend>
              <div class="indicator-line-options">
                <button
                  v-for="item in lineStyles"
                  :key="item.value"
                  type="button"
                  :class="{ selected: outputStyle(output.name).line_style === item.value }"
                  :aria-label="item.label"
                  @click="patchOutput(output.name, { line_style: item.value })"
                ><span :style="{ borderTopStyle: item.value === 'solid' ? 'solid' : item.value }" /></button>
              </div>
            </fieldset>
          </div>
        </div>
        <div v-if="outputs.length === 0" class="indicator-style-empty">该指标没有可设置的线条输出</div>
      </div>

      <footer>
        <button type="button" @click="emit('cancel')">取消</button>
        <button type="button" class="primary" @click="confirm">确认</button>
      </footer>
    </section>
  </div>
</template>
