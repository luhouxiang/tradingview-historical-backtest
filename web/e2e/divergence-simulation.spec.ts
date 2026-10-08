import { expect, test } from '@playwright/test'

test('automatic simulation hides future candles/fills, plays, seeks backwards and uses requested speed slider', async ({ page }) => {
  const revision = `sha256:${'a'.repeat(64)}`
  const timestamp = (n: number) => 1700000000000 + n * 300000
  const definition = { kind: 'strategy', algorithm_id: 'divergence_segment_end_simulation', algorithm_version: '1.0.0', source_hash: `sha256:${'b'.repeat(64)}` }
  await page.addInitScript(({ revision }) => Object.assign(window, { simulationFixtureProps: {
    dataset: { dataset_id: 'TEST', data_revision: revision, instrument: { symbol: 'AO' }, timeframe:'5m', source:{ timestamp_semantics:'bar_end' }, price: { price_scale: 1, price_decimals: 0 }, time: { timezone: 'Asia/Shanghai' }, coverage: { first_bar_index: 0, last_bar_index: 1000, bar_count: 1001 } },
    indicatorSources: [], strategySources: [], replayCursor: null, replayObjects: null, replaySignals: [],
  } }), { revision })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  let runs = 0
  let runFrom = 0
  const sessions = new Map<string, { job:string; cursor:number; head:number; from:number; snapshot:Record<string,unknown> }>()
  let commandNumber=0
  const fillFacts = (from:number,to:number) => {
    const actions = [[3,'open_long','买入开多'],[6,'close_long','卖出平多'],[6,'open_short','卖出开空']] as const
    return actions.filter(([n])=>n>=from&&n<=to).map(([n,action,label],i)=>({event_seq:n*10+i,known_at_bar_index:n,object_type:'chart_event',object_id:`fill-${n}-${i}`,operation:'upsert',object_revision:1,
      payload:{event_type:'simulation_fill',bar_index:n,timestamp_utc:timestamp(n),price_i64:2800,action,display_label:label,quantity:1,commission_i64:300,reason_code:'SIM_SEGMENT_END_DIVERGENCE',contract_symbol:'AO',position_after_side:action==='open_long'?'long':action==='open_short'?'short':'flat',position_after_quantity:action.startsWith('open')?1:0,closed_gross_pnl_i64:action.startsWith('open')?null:0,closed_net_pnl_i64:action.startsWith('open')?null:-600}}))
  }
  const buildSnapshot = (id:string,operation:string,from:number,cursor:number,head:number,begin:number,reset:boolean,state='live') => {
    const allFills=fillFacts(from,cursor), last=allFills.at(-1)?.payload
    const account={event_type:'simulation_account',known_at_bar_index:cursor,timestamp_utc:timestamp(cursor),position_side:last?.position_after_side??'flat',position_quantity:last?.position_after_quantity??0,equity_i64:100000000,available_i64:100000000,cash_i64:100000000,margin_i64:0,unrealized_pnl_i64:0,closed_trade_count:allFills.filter(f=>f.payload.action==='close_long').length,net_pnl_i64:0,realized_net_pnl_i64:0,total_commission_i64:allFills.length*300,total_slippage_i64:0,max_drawdown:0,win_rate:null}
    const events=[...fillFacts(reset?from:begin+1,cursor),{event_seq:cursor*10+9,known_at_bar_index:cursor,object_type:'chart_event',object_id:'SIM-ACCOUNT',operation:'upsert',object_revision:cursor+1,payload:account}]
    return {session_id:id,operation_id:operation,dataset_id:'TEST',data_revision:revision,run_id:id,run_signature:'hash',from_bar_index:from,to_bar_index:1000,cursor,processed_until_bar_index:head,state:cursor<head?'lookback':state,reset,events,account}
  }
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/algorithms')) { await route.fulfill({ json: { algorithms: [definition] } }); return }
    if (url.pathname.endsWith('/bars')) {
      const center = Number(url.searchParams.get('around_bar_index') ?? 500)
      const before = url.searchParams.get('before_bar_index')
      const after = url.searchParams.get('after_bar_index')
      const end = before ? Number(before)-1 : 1000
      const start = before ? url.searchParams.get('limit') === '1' ? end : 0 : after ? Number(after)+1 : Math.max(0,center-1500)
      const indices = Array.from({ length: Math.max(0,end-start+1) }, (_,i) => i+start)
      await route.fulfill({ json: { dataset_id: 'TEST', data_revision: revision, generation_id: url.searchParams.get('generation_id'), checksum: `sha256:${'c'.repeat(64)}`, price_scale: 1,
        has_more_before: false, has_more_after: false, coverage: { first_bar_index: start, last_bar_index: end },
        bars: { bar_index: indices, timestamp_utc: indices.map(timestamp), open_i64: indices.map(n => 2800+Math.round(Math.sin(n/5)*10)), high_i64: indices.map(n => 2804+Math.round(Math.sin(n/5)*10)), low_i64: indices.map(n => 2796+Math.round(Math.sin(n/5)*10)), close_i64: indices.map(n => 2801+Math.round(Math.sin(n/5)*10)), volume: indices.map(() => 100), open_interest: indices.map(() => null) } } }); return
    }
    if (url.pathname.endsWith('/training-sessions')) {
      runs++; const request = route.request().postDataJSON().backtest
      runFrom = request.range.from_bar_index
      expect(request.strategy.algorithm_id).toBe(definition.algorithm_id)
      expect(request.execution.fill_timing).toBe('next_bar_open')
      const id=`sim-e2e-${runs}`,job=`create-${runs}`
      sessions.set(id,{job,cursor:runFrom,head:runFrom,from:runFrom,snapshot:buildSnapshot(id,'create',runFrom,runFrom,runFrom,0,true)})
      await route.fulfill({status:202,json:{session_id:id,job_id:job,status:'queued'}});return
    }
    const training=url.pathname.match(/\/training-sessions\/([^/]+)(\/commands)?$/)
    if(training){
      const id=training[1]!,session=sessions.get(id)!
      if(training[2]){
        const request=route.request().postDataJSON(),begin=session.cursor
        expect(request.expected_cursor).toBe(begin)
        if(request.action==='advance'){expect(request.count).toBeLessThanOrEqual(100);session.cursor=Math.min(1000,begin+request.count);session.head=Math.max(session.head,session.cursor)}
        if(request.action==='seek'){expect(request.target_bar_index).toBeLessThanOrEqual(session.head);session.cursor=request.target_bar_index}
        session.job=`command-${++commandNumber}`
        session.snapshot=buildSnapshot(id,request.operation_id,session.from,session.cursor,session.head,begin,request.action!=='advance',request.action==='cancel'?'cancelled':'live')
        await route.fulfill({status:202,json:{session_id:id,job_id:session.job,status:'queued'}});return
      }
      await route.fulfill({json:{session_id:id,job_id:session.job,status:'completed',snapshot:session.snapshot}});return
    }
    expect(url.pathname).not.toContain('/backtests')
    await route.fulfill({ json: {} })
  })
  await page.goto('/e2e/divergence-simulation.html')
  const panel = page.getByLabel('三类背驰自动交易模拟')
  await expect(panel.getByRole('button', { name:'开始自动交易模拟' })).toBeEnabled()
  await panel.getByRole('button', { name:'开始自动交易模拟' }).click()
  await expect(panel).toContainText('逐根训练')
  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toHaveAttribute('data-cache-last-index','0')
  await expect(panel).toContainText('已成交 0 次')
  const speed = page.getByLabel('模拟回放速度')
  await expect(speed).toHaveValue('50')
  await page.getByRole('button', { name:'播放模拟',exact:true }).click()
  await expect(chart).toHaveAttribute('data-cache-last-index','1')
  await page.getByRole('button', { name:'暂停模拟',exact:true }).click()
  await panel.getByLabel('模拟跳转K线').fill('6')
  await panel.getByRole('button', { name:'跳转',exact:true }).click()
  await expect(chart).toHaveAttribute('data-cache-last-index','6')
  await expect(panel).toContainText('空仓（做空） 1 手')
  await expect(panel.locator('tbody tr')).toHaveCount(3)
  await expect(panel.getByRole('columnheader', { name:'时间',exact:true })).toBeVisible()
  await expect(panel.getByRole('columnheader', { name:'成交后持仓',exact:true })).toBeVisible()
  await expect(panel.locator('tbody tr').nth(1)).toContainText('-6.00')
  await expect(panel.locator('tbody tr').nth(1)).toContainText('空仓')
  await expect(chart.locator('.replay-signal.open_long text')).toHaveText('买入开多')
  await expect(chart.locator('.replay-signal.close_long text')).toHaveText('卖出平多')
  await expect(chart.locator('.replay-signal.open_short text')).toHaveText('卖出开空')
  expect(await chart.locator('.replay-signal.close_long text').getAttribute('y')).not.toBe(await chart.locator('.replay-signal.open_short text').getAttribute('y'))
  await speed.fill('100')
  await expect(page.getByLabel('K线模拟速度控制')).toContainText('100 根/秒')
  await page.getByRole('button', { name:'播放模拟',exact:true }).click()
  await expect.poll(async () => Number(await chart.getAttribute('data-cache-last-index'))).toBeGreaterThan(50)
  await page.getByRole('button', { name:'暂停模拟',exact:true }).click()
  await speed.fill('0')
  await expect(page.getByLabel('K线模拟速度控制')).toContainText('60 秒/根')
  await page.screenshot({ path:'../trading-data/acceptance/divergence-simulation-16b-ledger.png', fullPage:true })
  await panel.getByLabel('模拟跳转K线').fill('2'); await panel.getByRole('button', { name:'跳转',exact:true }).click()
  await expect(chart).toHaveAttribute('data-cache-last-index','2')
  await expect(panel.locator('tbody tr')).toHaveCount(0)
  expect(runs).toBe(1)
  await page.getByRole('button', { name:'退出模拟',exact:true }).click()
  await expect(chart).toHaveAttribute('data-cache-last-index','1000')
  await panel.getByLabel('复盘训练日期').fill('2023-11-17')
  await panel.getByRole('button', { name:'更换至此日期',exact:true }).click()
  const target = Math.ceil((Date.UTC(2023,10,16,16)-timestamp(0))/300000)
  await expect(chart).toHaveAttribute('data-cache-last-index',String(target))
  await expect(panel.locator('tbody tr')).toHaveCount(0)
  expect(runs).toBe(2)
  expect(errors).toEqual([])
})
