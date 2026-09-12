<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getDataset } from '../api/client'
import { createBacktestWorkspaceChannel, type BacktestWorkspaceMessage } from '../backtest/workspaceChannel'
import BacktestPanel from '../components/BacktestPanel.vue'
import type { BacktestTrade, DatasetMeta, StrategyRunSource } from '../types/api'

const route = useRoute()
const dataset = ref<DatasetMeta | null>(null)
const error = ref('')
const linkError = ref('')
const channel = createBacktestWorkspaceChannel()

function publish(message: BacktestWorkspaceMessage): void {
  try {
    const transferableMessage = JSON.parse(JSON.stringify(message)) as BacktestWorkspaceMessage
    channel?.postMessage(transferableMessage)
    linkError.value = ''
  } catch (cause) {
    linkError.value = cause instanceof Error ? cause.message : '跨窗口联动失败'
  }
}

function completed(source: StrategyRunSource): void {
  if (!dataset.value) return
  publish({
    type: 'run-completed', dataset_id: dataset.value.dataset_id,
    data_revision: dataset.value.data_revision, source,
  })
}

function focusTrade(trade: BacktestTrade, leg: 'entry' | 'exit'): void {
  if (!dataset.value) return
  publish({
    type: 'focus-trade', dataset_id: dataset.value.dataset_id,
    data_revision: dataset.value.data_revision, trade, leg,
  })
}

onMounted(async () => {
  const datasetId = String(route.query.dataset_id ?? '')
  const revision = String(route.query.revision ?? '')
  if (!datasetId || !/^sha256:[0-9a-f]{64}$/.test(revision)) {
    error.value = '回测页面缺少有效的数据集绑定信息，请从 K 线页面重新打开。'
    return
  }
  try {
    dataset.value = await getDataset(datasetId, revision)
    document.title = `回测 · ${dataset.value.instrument.symbol} · TVBT`
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '绑定数据集读取失败'
  }
})

onBeforeUnmount(() => channel?.close())
</script>

<template>
  <main class="backtest-workspace-page">
    <header class="backtest-workspace-header">
      <div>
        <strong>策略回测</strong>
        <span v-if="dataset">已绑定 {{ dataset.dataset_id }} · {{ dataset.timeframe }} · revision {{ dataset.data_revision.slice(7, 19) }}</span>
        <span v-else>正在连接 K 线工作区…</span>
      </div>
      <small>双击任一交易，原 K 线窗口将定位到该笔入场位置</small>
    </header>
    <p v-if="error" class="backtest-workspace-error">{{ error }}</p>
    <p v-if="linkError" class="backtest-workspace-error">K 线窗口联动失败：{{ linkError }}</p>
    <BacktestPanel
      v-else-if="dataset" :dataset="dataset" view="workspace"
      @completed="completed" @focus-trade="focusTrade"
    />
  </main>
</template>
