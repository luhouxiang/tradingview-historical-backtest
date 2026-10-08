import { expect,test } from '@playwright/test'

test('simulation window does not compress chart, shares controls, reuses window and exits on close',async({page,context})=>{
  const revision=`sha256:${'a'.repeat(64)}`,id='SHFE.AOL9.5m'
  const dataset={dataset_id:id,data_revision:revision,instrument:{symbol:'AOL9'},timeframe:'5m',source:{format:'tdx',encoding:'GB18030',timestamp_semantics:'bar_end'},price:{price_scale:1,price_decimals:0},time:{timezone:'Asia/Shanghai'},coverage:{first_bar_index:0,last_bar_index:100,first_timestamp_utc:1700000000000,last_timestamp_utc:1700030000000}}
  let cursor=0,job='create',sessionState='live',creates=0
  await context.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()),method=route.request().method()
    if(url.pathname.endsWith('/health'))return route.fulfill({json:{status:'ok',services:{}}})
    if(url.pathname.endsWith('/datasets'))return route.fulfill({json:{catalog_revision:1,datasets:[{dataset_id:id,active_revision:revision,instrument:'AOL9',timeframe:'5m',status:'ready',bar_count:101}]}})
    if(url.pathname.endsWith(`/${id}`))return route.fulfill({json:dataset})
    if(url.pathname.endsWith('/source-files'))return route.fulfill({json:{items:[]}})
    if(url.pathname.endsWith('/algorithms'))return route.fulfill({json:{algorithms:[{kind:'strategy',algorithm_id:'divergence_segment_end_simulation',algorithm_version:'1.2.0',source_hash:`sha256:${'b'.repeat(64)}`} ]}})
    if(url.pathname.endsWith('/bars')){
      const bars=Array.from({length:101},(_,n)=>n)
      return route.fulfill({json:{dataset_id:id,data_revision:revision,generation_id:url.searchParams.get('generation_id'),checksum:`sha256:${'c'.repeat(64)}`,price_scale:1,has_more_before:false,has_more_after:false,coverage:{first_bar_index:0,last_bar_index:100},bars:{bar_index:bars,timestamp_utc:bars.map(n=>1700000000000+n*300000),open_i64:bars.map(()=>3000),high_i64:bars.map(()=>3003),low_i64:bars.map(()=>2998),close_i64:bars.map(()=>3001),volume:bars.map(()=>100),open_interest:bars.map(()=>null)}}})
    }
    if(url.pathname.endsWith('/training-sessions')){creates++;return route.fulfill({status:202,json:{session_id:'simulation-popup',job_id:job,status:'queued'}})}
    if(url.pathname.endsWith('/commands')){
      const command=route.request().postDataJSON();job=command.operation_id
      if(command.action==='advance')cursor+=command.count
      if(command.action==='cancel')sessionState='cancelled'
      return route.fulfill({status:202,json:{session_id:'simulation-popup',job_id:job,status:'queued'}})
    }
    if(url.pathname.endsWith('/simulation-popup'))return route.fulfill({json:{session_id:'simulation-popup',job_id:job,status:'completed',snapshot:{session_id:'simulation-popup',dataset_id:id,data_revision:revision,cursor,processed_until_bar_index:cursor,state:sessionState,reset:cursor===0,events:cursor===1?[{event_seq:1,known_at_bar_index:1,object_type:'chart_event',object_id:'fill-1',object_revision:1,operation:'upsert',payload:{event_type:'simulation_fill',bar_index:1,timestamp_utc:1700000300000,price_i64:3001,action:'open_long',quantity:1,commission_i64:300,position_after_side:'long',position_after_quantity:1,closed_net_pnl_i64:null,reason_code:'SIM_SEGMENT_END_DIVERGENCE'}}]:[],account:{equity_i64:100000000,available_i64:100000000}}}})
    if(method==='GET'&&/layout|drawings|strategy-source-config/.test(url.pathname))return route.fulfill({status:404,json:{error:{code:'WORKSPACE_NOT_FOUND',message:'test fixture'}}})
    return route.fulfill({json:{}})
  })
  await page.goto('/')
  const opener=page.getByRole('button',{name:'打开独立自动交易模拟窗口',exact:true})
  await expect(page.locator('.dataset-meta')).toContainText(id)
  const popupPromise=page.waitForEvent('popup')
  await opener.click()
  const popup=await popupPromise
  await expect(popup).toHaveTitle('自动交易模拟 · 策略复盘训练')
  await expect(page.locator('.bottom-dock')).not.toHaveClass(/expanded/)
  await expect(page.locator('.simulation-panel')).toHaveCount(0)
  const initialHeight=(await page.getByLabel('图表工作区').boundingBox())!.height
  await popup.getByRole('button',{name:'开始自动交易模拟',exact:true}).click()
  await expect(page.getByRole('button',{name:'单步',exact:true})).toBeEnabled()
  await page.getByRole('button',{name:'单步',exact:true}).click()
  await expect(popup.getByLabel('模拟跳转K线')).toHaveValue('1')
  await expect(page.getByLabel('K线模拟速度控制')).toContainText('K1 / 100')
  await page.getByRole('button',{name:'单步',exact:true}).click()
  await expect(popup.getByLabel('模拟跳转K线')).toHaveValue('2')
  const fill=popup.locator('[data-fill-id="fill-1"]')
  await fill.click()
  await expect(fill).toHaveClass(/selected/)
  await fill.dblclick()
  await expect(page.locator('.signal-selection')).toContainText('买入')
  await expect(popup.getByLabel('模拟跳转K线')).toHaveValue('2')
  expect(cursor).toBe(2)
  const chartBox=(await page.locator('.chart-host').boundingBox())
  const selection=page.locator('.signal-selection line').first()
  const selectionX=Number(await selection.getAttribute('x1'))
  expect(chartBox).not.toBeNull()
  expect(selectionX).toBeGreaterThan(100)
  expect(Math.abs(selectionX-(chartBox!.width-64)/2)).toBeLessThan(50)
  expect((await page.getByLabel('图表工作区').boundingBox())!.height).toBeGreaterThanOrEqual(initialHeight-36)
  await opener.click()
  expect(context.pages()).toHaveLength(2)
  expect(creates).toBe(1)
  await page.screenshot({path:'../trading-data/acceptance/simulation-window-chart.png',fullPage:true})
  await popup.screenshot({path:'../trading-data/acceptance/simulation-window-ledger.png',fullPage:true})
  await popup.close()
  await expect(page.getByLabel('K线模拟速度控制')).toHaveCount(0)
  await expect.poll(()=>sessionState).toBe('cancelled')
})
