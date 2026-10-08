<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { cancelJob, commandTrainingSession, createTrainingSession, getTrainingSession, getBars, listAlgorithms } from '../api/client'
import { capitalConfig, executionRequest } from '../execution/config'
import { ReplayEventIndex, type ReplayObjects, type ReplaySignal } from '../replay/eventIndex'
import { SimulationClock, barsPerSecond } from '../replay/simulationSpeed'
import { firstBarOnOrAfterDate, localTrainingDate } from '../replay/trainingDate'
import type { AlgorithmDefinition, DatasetMeta, EquityRow, TrainingAccepted, TrainingCommand, TrainingSnapshot } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; followCursor?: (cursor: number) => Promise<void> }>()
const emit = defineEmits<{
  update: [value: { cursor: number | null; objects: ReplayObjects | null; signals: ReplaySignal[] }]
  focusFill: [fill: ReplaySignal | null]
  controls: [value: { active: boolean; ready: boolean; preparing: boolean; pendingPlay: boolean; status: string; playing: boolean; speed: number; cursor: number; to: number }]
}>()
const algorithm = ref<AlgorithmDefinition | null>(null)
const from = ref(0), to = ref(0), cursor = ref(0), jump = ref(0)
const speed = ref(50), playing = ref(false), active = ref(false), ready = ref(false)
const preparing = ref(false), pendingPlay = ref(false)
const trainingDate = ref(''), locating = ref(false)
const parametersOpen = ref(true), reportOpen = ref(false)
const status = ref('等待开始'), error = ref(''), runId = ref('')
const quantity = ref(1), initialCash = ref(100000000), commission = ref(300), slippage = ref(1)
const trend = ref(true), consolidation = ref(true), oscillation = ref(true), allowShort = ref(true)
const visibleSignals = ref<ReplaySignal[]>([]), equity = ref<EquityRow[]>([])
let index: ReplayEventIndex | null = null
let generation = 0, timer = 0, publication = 0, playback = 0
let sessionId = ''
let preparationJob = ''
let inFlight: Promise<TrainingSnapshot> | null = null
const advancing = ref(false), processedUntil = ref(0)
const executable = ref(true)
const selectedFillId = ref<string | null>(null)
let fillFocusRequest = 0, focusingFill = false
function clearFillFocus(): void {
  fillFocusRequest++; focusingFill = false; selectedFillId.value = null
  emit('focusFill', null)
}
async function focusFill(fill: ReplaySignal): Promise<void> {
  pause()
  selectedFillId.value = fill.object_id
  focusingFill = true
  const request = ++fillFocusRequest, currentGeneration = generation
  await inFlight?.catch(() => undefined)
  await nextTick()
  if (request !== fillFocusRequest || generation !== currentGeneration || !active.value
    || !fills.value.some(item => item.object_id === fill.object_id)) return
  emit('focusFill', fill)
}
class TrainingFailure extends Error {
  constructor(message:string, readonly snapshot:TrainingSnapshot|null) { super(message) }
}
const clock = new SimulationClock()
const busy = computed(() => advancing.value || locating.value || preparing.value)
const fills = computed(() => visibleSignals.value.filter(signal => signal.event_type === 'simulation_fill'))
const rejected = computed(() => visibleSignals.value.filter(signal => signal.event_type === 'simulation_order_rejected'))
const account = computed(() => visibleSignals.value.find(signal => signal.event_type === 'simulation_account'))
const currentEquity = computed(() => {
  let low = 0, high = equity.value.length - 1, found: EquityRow | null = null
  while (low <= high) {
    const mid = (low + high) >> 1, row = equity.value[mid]!
    if (row.bar_index <= cursor.value) { found = row; low = mid + 1 } else high = mid - 1
  }
  return found
})
const position = computed(() => {
  if (account.value) return holding(account.value.position_side, account.value.position_quantity)
  const last = fills.value.at(-1)
  return last ? holding(last.position_after_side, last.position_after_quantity) : '空仓'
})
function holding(side: unknown, quantity: unknown): string {
  if (side === 'flat' && quantity === 0) return '空仓'
  if ((side === 'long' || side === 'short') && typeof quantity === 'number') return `${side === 'long' ? '多仓' : '空仓（做空）'} ${quantity} 手`
  return '持仓未留档'
}
function money(value: unknown): string {
  if (value === null) return '—'
  return typeof value === 'number' ? (value / 100).toFixed(2) : '未留档'
}
function tradeTime(value: unknown): string {
  if (typeof value !== 'number') return '未留档'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: props.dataset?.time?.timezone ?? 'Asia/Shanghai',
    year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23' }).format(value)
}
const buys = new Set(['open_long', 'close_short', 'add_long', 'reduce_short'])
const opens = new Set(['open_long', 'open_short', 'add_long', 'add_short'])
const latestDecision = computed(() => [...visibleSignals.value].reverse().find(signal => signal.object_type === 'stage_signal'))
const kindLabels: Record<string, string> = { trend: '趋势背驰', consolidation: '盘整背驰', center_oscillation: '震荡背驰' }
const reasonLabels: Record<string, string> = {
  SIM_SEGMENT_END_DIVERGENCE: '对应段已确认结束，结构与力度条件通过',
  SIM_OPPOSITE_DIVERGENCE: '出现反向背驰，先平仓再反手', SIM_SOURCE_REVISED: '来源结构已修订，退出原仓位',
}
const rejectionLabels: Record<string, string> = {
  NO_NEXT_BAR: '区间结束，无下一根K线可成交', INSUFFICIENT_MARGIN: '保证金或手续费资金不足',
  POSITION_STATE_MISMATCH: '订单与实际持仓状态不匹配', REDUCE_QUANTITY_EXCEEDS_POSITION: '平仓手数超过实际持仓',
}

function controls(): void { emit('controls', { active: active.value, ready: ready.value && executable.value, preparing: preparing.value, pendingPlay:pendingPlay.value, status:status.value, playing: playing.value, speed: speed.value, cursor: cursor.value, to: to.value }) }
function pause(): void { pendingPlay.value = false; playing.value = false; playback++; window.clearTimeout(timer); controls() }
function exit(): void {
  clearFillFocus()
  const previousSession = sessionId, previousFlight = inFlight
  if (preparationJob) void cancelJob(preparationJob).catch(() => undefined)
  preparationJob = ''
  pause(); active.value = false; ready.value = false; generation++; publication++
  sessionId = ''; inFlight = null; advancing.value = false
  preparing.value = false
  executable.value = true
  // Finish the already accepted bounded command before cancelling the session.
  // It cannot publish into the new date/source because generation changed.
  if (previousSession) void (async () => {
    try {
      await previousFlight?.catch(() => undefined)
      const state = await getTrainingSession(previousSession)
      if (state.snapshot && !['completed', 'cancelled'].includes(state.snapshot.state)) {
        await commandTrainingSession({ session_id: previousSession, operation_id: operationId(), action:'cancel', expected_cursor:state.snapshot.cursor })
      }
    } catch { /* Preserve the last committed prefix if the service is unavailable. */ }
  })()
  status.value = '等待开始'; error.value = ''; runId.value = ''
  locating.value = false
  reportOpen.value = false
  visibleSignals.value = []; equity.value = []; index = null
  emit('update', { cursor: null, objects: null, signals: [] }); controls()
}
function operationId(): string { return crypto.randomUUID().replaceAll('-', '') }
async function waitForCommand(accepted: TrainingAccepted, onWait?:()=>void): Promise<TrainingSnapshot> {
  for (;;) {
    const value = await getTrainingSession(accepted.session_id)
    if (value.job_id !== accepted.job_id) throw new Error('训练命令身份已变更')
    if (value.status === 'completed' && value.snapshot) return value.snapshot
    if (['failed','cancelled','interrupted'].includes(value.status)) throw new TrainingFailure(value.error?.message ?? '训练命令未完成', value.snapshot?.state === 'interrupted' ? value.snapshot : null)
    onWait?.()
    await new Promise(resolve => window.setTimeout(resolve, 100))
  }
}
async function applySnapshot(snapshot: TrainingSnapshot, requestGeneration: number): Promise<void> {
  if (generation !== requestGeneration || sessionId !== snapshot.session_id) return
  if (snapshot.dataset_id !== props.dataset?.dataset_id || snapshot.data_revision !== props.dataset?.data_revision) throw new Error('训练数据身份不一致')
  if (snapshot.reset || !index) index = new ReplayEventIndex(snapshot.events)
  else index.append(snapshot.events)
  processedUntil.value = snapshot.processed_until_bar_index
  executable.value = !['interrupted','cancelled'].includes(snapshot.state)
  const a = snapshot.account
  equity.value = [{ bar_index:snapshot.cursor, timestamp_utc:Number(a.timestamp_utc), equity_i64:Number(a.equity_i64), cash_i64:Number(a.cash_i64), available_i64:Number(a.available_i64), margin_i64:Number(a.margin_i64), drawdown:Number(a.max_drawdown ?? 0) }]
  status.value = snapshot.state === 'lookback' ? '记录回看（不重新成交）' : snapshot.state === 'completed' ? '训练完成' : snapshot.state === 'interrupted' ? '训练已中断（已成交记录保留）' : snapshot.state === 'cancelled' ? '训练已取消' : '逐根训练'
  await publish(snapshot.cursor)
}
async function issue(command: Omit<TrainingCommand, 'session_id' | 'operation_id' | 'expected_cursor'>): Promise<void> {
  if (!sessionId || inFlight) return
  const requestGeneration = generation
  const request: TrainingCommand = { ...command, session_id:sessionId, operation_id:operationId(), expected_cursor:cursor.value }
  advancing.value = true
  status.value = command.action === 'seek' ? '正在回看记录' : '正在推进（含等待引擎）'
  const pending = commandTrainingSession(request).then(waitForCommand)
  inFlight = pending
  try { await applySnapshot(await pending, requestGeneration) }
  catch (cause) {
    if (cause instanceof TrainingFailure && cause.snapshot && generation === requestGeneration) {
      await applySnapshot(cause.snapshot,requestGeneration)
      error.value = cause.message
    }
    throw cause
  }
  finally { if (generation === requestGeneration && inFlight === pending) { inFlight = null; advancing.value = false; controls() } }
}
async function publish(next: number): Promise<void> {
  if (!index) return
  const currentGeneration = generation
  const request = ++publication
    cursor.value = Math.max(from.value, Math.min(to.value, next)); jump.value = cursor.value
    const objects = index.seek(cursor.value)
    visibleSignals.value = index.signals()
    emit('update', { cursor: cursor.value, objects, signals: visibleSignals.value })
    await nextTick()
    if (generation !== currentGeneration || publication !== request) return
    if (selectedFillId.value && !fills.value.some(fill => fill.object_id === selectedFillId.value)) clearFillFocus()
    if (!focusingFill) await props.followCursor?.(cursor.value)
    if (generation !== currentGeneration || publication !== request) return
    if (cursor.value >= to.value) pause()
    controls()
}
async function tick(token: number): Promise<void> {
  if (!playing.value || token !== playback) return
  const count = clock.advance(performance.now(), barsPerSecond(speed.value))
  try { if (count > 0) await issue({ action:'advance', count:Math.min(100,count,to.value-cursor.value) }) }
  catch (cause) { if (token !== playback) return; pause(); error.value = cause instanceof Error ? cause.message : 'K线加载失败' }
  if (playing.value && token === playback) timer = window.setTimeout(() => void tick(token), 50)
}
function togglePlay(): void {
  if (preparing.value) { pendingPlay.value = !pendingPlay.value; controls(); return }
  if (playing.value) { pause(); return }
  if (!ready.value || !executable.value || cursor.value >= to.value) return
  clearFillFocus()
  playing.value = true; clock.start(performance.now()); controls()
  const token = ++playback
  timer = window.setTimeout(() => void tick(token), 50)
}
function setSpeed(value: number): void { speed.value = Math.max(0, Math.min(100, value)); clock.start(performance.now()); controls() }
async function seek(next: number): Promise<void> {
  if (!ready.value || busy.value || !Number.isSafeInteger(next)) return
  clearFillFocus()
  pause()
  const requestGeneration = generation
  const target = Math.max(from.value,Math.min(to.value,next))
  if (!executable.value && target > processedUntil.value) { error.value = '会话已中断，只能回看已执行记录；请重新开始训练'; return }
  try {
    if (target <= processedUntil.value) await issue({ action:'seek', target_bar_index:target })
    else while (generation === requestGeneration && cursor.value < target) await issue({ action:'advance', count:Math.min(100,target-cursor.value) })
  }
  catch (cause) { if (requestGeneration === generation) error.value = cause instanceof Error ? cause.message : 'K线加载失败' }
}
async function step(): Promise<void> { await seek(cursor.value + 1) }

async function locateDate(): Promise<void> {
  const dataset = props.dataset
  if (!dataset || !trainingDate.value || busy.value) return
  pause(); locating.value = true; error.value = ''
  const request = generation, timezone = dataset.time?.timezone ?? 'Asia/Shanghai'
  try {
    const target = await firstBarOnOrAfterDate(trainingDate.value, dataset.coverage.first_bar_index, dataset.coverage.last_bar_index, async index => {
      if (request !== generation) throw new Error('日期定位已取消')
      const response = await getBars(dataset.dataset_id, dataset.data_revision, `training-date-${request}`, { beforeBarIndex:index+1, limit:1 })
      if (response.dataset_id !== dataset.dataset_id || response.data_revision !== dataset.data_revision || response.bars.bar_index[0] !== index) throw new Error('日期定位行情身份或索引不一致')
      return localTrainingDate(response.bars.timestamp_utc[0]!, timezone)
    })
    if (request !== generation) return
    exit(); from.value = target
    await start()
  } catch (cause) { if (request === generation) error.value = cause instanceof Error ? cause.message : '日期定位失败' }
  finally { if (request === generation) locating.value = false }
}

async function start(): Promise<void> {
  const dataset = props.dataset, definition = algorithm.value
  if (!dataset || !definition || ![trend.value, consolidation.value, oscillation.value].some(Boolean)) return
  if (![from.value, to.value, quantity.value, initialCash.value, commission.value, slippage.value].every(Number.isSafeInteger)
    || from.value < dataset.coverage.first_bar_index || to.value > dataset.coverage.last_bar_index || from.value > to.value
    || quantity.value < 1 || quantity.value > 100 || initialCash.value < 1 || commission.value < 0 || slippage.value < 0) {
    error.value = '请检查起止K线、手数、资金和成本范围'; return
  }
  exit()
  const requestGeneration = ++generation
  const identity = `${dataset.dataset_id}:${dataset.data_revision}`
  const current = () => requestGeneration === generation && identity === `${props.dataset?.dataset_id}:${props.dataset?.data_revision}`
  active.value = true; preparing.value = true; status.value = '正在提交起点准备请求'; error.value = ''; runId.value = ''
  // Hide all future candles immediately, including while preparation runs.
  cursor.value = from.value; jump.value = from.value; controls()
  const empty = new ReplayEventIndex([]).seek(cursor.value)
  emit('update', { cursor: cursor.value, objects: empty, signals: [] })
  try {
    const accepted = await createTrainingSession({ operation_id:operationId(), backtest: {
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      strategy: { kind: 'strategy', algorithm_id: definition.algorithm_id, algorithm_version: definition.algorithm_version, source_hash: definition.source_hash },
      parameters: { checkpoint_interval: 1024, enable_trend: trend.value, enable_consolidation: consolidation.value,
        enable_oscillation: oscillation.value, operation_quantity: quantity.value, allow_short: allowShort.value },
      range: { warmup_from_bar_index: dataset.coverage.first_bar_index, from_bar_index: from.value, to_bar_index: to.value },
      execution: executionRequest({ commissionAmountI64: commission.value, slippageTicks: slippage.value }),
      capital: capitalConfig(initialCash.value), random_seed: 16,
    } })
    const preparationStarted = performance.now()
    const preparation = waitForCommand(accepted, () => {
      if (!current() || !preparing.value) return
      const nextStatus = `准备到 K${from.value} · 已等待 ${Math.floor((performance.now()-preparationStarted)/1000)} 秒（从历史起点完整预热）`
      if (status.value !== nextStatus) { status.value = nextStatus; controls() }
    })
    if (!current()) {
      void cancelJob(accepted.job_id).catch(() => undefined)
      void preparation.then(snapshot => commandTrainingSession({ session_id:accepted.session_id, operation_id:operationId(), action:'cancel', expected_cursor:snapshot.cursor })).catch(() => undefined)
      return
    }
    sessionId = accepted.session_id; runId.value = accepted.session_id
    preparationJob = accepted.job_id
    inFlight = preparation; status.value = `准备到 K${from.value}（完整历史预热，完成后才能推进）`; controls()
    const snapshot = await preparation
    if (!current()) return
    inFlight = null
    preparationJob = ''
    preparing.value = false
    const shouldPlay = pendingPlay.value
    const queuedPlaybackToken = playback
    pendingPlay.value = false
    ready.value = true; parametersOpen.value = false; controls()
    await applySnapshot(snapshot,requestGeneration)
    if (current() && shouldPlay && playback === queuedPlaybackToken) togglePlay()
  } catch (cause) {
    if (!current()) return
    inFlight = null
    preparing.value = false
    pause(); status.value = '失败'; error.value = cause instanceof Error ? cause.message : '模拟失败'; controls()
  }
}
watch(() => [props.dataset?.dataset_id, props.dataset?.data_revision], () => {
  exit(); from.value = props.dataset?.coverage.first_bar_index ?? 0
  to.value = props.dataset?.coverage.last_bar_index ?? 0; cursor.value = from.value; jump.value = from.value
  status.value = '等待开始'; runId.value = ''; controls()
  parametersOpen.value = true
  const timestamp = props.dataset?.coverage.first_timestamp_utc
  trainingDate.value = typeof timestamp === 'number' ? localTrainingDate(timestamp, props.dataset?.time?.timezone ?? 'Asia/Shanghai') : ''
}, { immediate: true })
onMounted(async () => {
  try { algorithm.value = (await listAlgorithms()).find(value => value.algorithm_id === 'divergence_segment_end_simulation') ?? null }
  catch { error.value = '模拟策略目录加载失败' }
})
onBeforeUnmount(exit)
defineExpose({ togglePlay, step, setSpeed, exit })
</script>

<template>
  <section class="replay-panel simulation-panel" aria-label="三类背驰自动交易模拟">
    <div class="training-date-controls"><strong>策略复盘训练 · 自动下单</strong><span>{{ algorithm?.name ?? '三类背驰段结束策略' }}</span><button @click="parametersOpen = !parametersOpen">交易参数</button><button :disabled="!ready" @click="reportOpen = !reportOpen">分析报告</button></div>
    <details class="simulation-details"><summary>交易逻辑说明 · 收盘决策，下一根开盘成交</summary>
      <p class="simulation-logic">交易逻辑：所选三类底背驰开多，{{ allowShort ? '顶背驰开空；反向信号先平再反手' : '顶背驰只平多' }}；同向不加仓。段被确认结束后决策，下一根开盘成交。来源结构失效时退出。MACD仅辅助。</p>
      <p>播放或单步才推进策略与成交。暂停不发送新推进命令，已接受的有界批次可以完成。回看已执行K线不重复下单；“段结束”使用实时确认K线，不在事后极值K线上回填交易。</p>
      <p v-if="latestDecision">最近决策：{{ kindLabels[String(latestDecision.divergence_kind)] }}，端点 K{{ latestDecision.segment_end_bar_index }}，确认 K{{ latestDecision.segment_confirmed_at_bar_index }}，{{ reasonLabels[String(latestDecision.reason_code)] }}。</p>
    </details>
    <div class="training-date-controls">
      <label>复盘日期（{{ dataset?.time?.timezone ?? 'Asia/Shanghai' }}自然日）<input v-model="trainingDate" aria-label="复盘训练日期" type="date" :disabled="busy" /></label>
      <button :disabled="busy || !dataset || !algorithm || !trainingDate" @click="locateDate">更换至此日期</button>
    </div>
    <div v-show="parametersOpen" class="replay-range">
      <label>开始K <input v-model.number="from" type="number" :disabled="busy || ready" :min="dataset?.coverage.first_bar_index" :max="to" /></label>
      <label>结束K <input v-model.number="to" type="number" :disabled="busy || ready" :min="from" :max="dataset?.coverage.last_bar_index" /></label>
      <label><input v-model="trend" type="checkbox" :disabled="busy || ready" />趋势背驰</label>
      <label><input v-model="consolidation" type="checkbox" :disabled="busy || ready" />盘整背驰</label>
      <label><input v-model="oscillation" type="checkbox" :disabled="busy || ready" />震荡背驰</label>
      <label><input v-model="allowShort" type="checkbox" :disabled="busy || ready" />允许做空</label>
      <label>手数 <input v-model.number="quantity" type="number" min="1" max="100" :disabled="busy || ready" /></label>
      <label>初始资金（分） <input v-model.number="initialCash" type="number" min="1" :disabled="busy || ready" /></label>
      <label>每手手续费（分） <input v-model.number="commission" type="number" min="0" :disabled="busy || ready" /></label>
      <label>滑点（跳） <input v-model.number="slippage" type="number" min="0" :disabled="busy || ready" /></label>
      <button :disabled="busy || !dataset || !algorithm || !(trend || consolidation || oscillation)" @click="start">开始自动交易模拟</button>
    </div>
    <div class="replay-controls">
      <button :disabled="!ready || busy || cursor <= from" @click="seek(cursor - 1)">后退</button>
      <input v-model.number="jump" aria-label="模拟跳转K线" type="number" :min="from" :max="to" />
      <button :disabled="!ready || busy" @click="seek(jump)">跳转</button>
      <span :title="runId">{{ status }} · 会话 {{ runId.slice(-8) }} · {{ position }} · 已成交 {{ fills.length }} 次</span>
      <span v-if="currentEquity">当前权益 {{ (currentEquity.equity_i64 / 100).toFixed(2) }} 元 · 可用 {{ (currentEquity.available_i64 / 100).toFixed(2) }} 元</span>
      <span v-if="account">浮动盈亏 {{ money(account.unrealized_pnl_i64) }} 元 · 保证金 {{ money(account.margin_i64) }} 元</span>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>
    <p v-if="rejected.length" class="issue">未成交 {{ rejected.length }} 次；最近原因：{{ rejectionLabels[String(rejected.at(-1)?.reason_code)] ?? rejected.at(-1)?.reason_code }}。持仓只按实际成交记录更新。</p>
    <section v-if="reportOpen" aria-label="当前复盘训练分析报告">
      <p>分析截止：{{ tradeTime(account?.timestamp_utc) }} · K{{ cursor }}（不包含后续行情）</p>
      <p v-if="!account">此运行未留档训练报告，请使用新策略版本重新开始训练。</p>
      <p v-else>已平仓交易 {{ account.closed_trade_count }} 笔 · 胜率 {{ typeof account.win_rate === 'number' ? `${(account.win_rate * 100).toFixed(1)}%` : '—' }} · 已实现净盈亏 {{ money(account.realized_net_pnl_i64) }} 元 · 含浮盈总盈亏 {{ money(account.net_pnl_i64) }} 元 · 最大回撤 {{ typeof account.max_drawdown === 'number' ? `${(account.max_drawdown * 100).toFixed(2)}%` : '未留档' }} · 累计手续费 {{ money(account.total_commission_i64) }} 元 · 滑点成本 {{ money(account.total_slippage_i64) }} 元（已体现在成交价，不再从盈亏重复扣除）。</p>
    </section>
    <table class="simulation-fills" aria-label="策略复盘训练成交账本"><thead><tr><th>时间</th><th>合约</th><th>买卖</th><th>开平</th><th>成交价</th><th>手数</th><th>成交后持仓</th><th>平仓盈亏（净／元）</th><th>手续费（元）</th><th>成交原因</th></tr></thead>
      <tbody><tr v-for="fill in [...fills].reverse()" :key="fill.object_id" :data-fill-id="fill.object_id"
        tabindex="0" :aria-selected="selectedFillId === fill.object_id" :class="{ selected: selectedFillId === fill.object_id }"
        title="单击选中，双击定位成交 K 线（暂停播放，不改变训练进度）"
        @click="selectedFillId = fill.object_id" @dblclick="focusFill(fill)" @keydown.enter.prevent="focusFill(fill)"
        @keydown.space.prevent="selectedFillId = fill.object_id"><td :title="`K${fill.bar_index}`">{{ tradeTime(fill.timestamp_utc) }}</td><td>{{ fill.contract_symbol ?? dataset?.instrument?.symbol ?? dataset?.dataset_id }}</td><td>{{ buys.has(String(fill.action)) ? '买' : '卖' }}</td><td>{{ opens.has(String(fill.action)) ? '开仓' : '平仓' }}</td><td>{{ Number(fill.price_i64) / (dataset?.price.price_scale ?? 1) }}</td><td>{{ fill.quantity }}</td><td>{{ holding(fill.position_after_side, fill.position_after_quantity) }}</td><td :title="`毛盈亏 ${money(fill.closed_gross_pnl_i64)} 元；滑点已含在成交价中`">{{ money(fill.closed_net_pnl_i64) }}</td><td>{{ money(fill.commission_i64) }}</td><td>{{ reasonLabels[String(fill.reason_code)] }}</td></tr></tbody>
    </table>
  </section>
</template>
