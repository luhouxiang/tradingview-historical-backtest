import { expect, test } from '@playwright/test'

test('late-date preparation shows its cursor and explicit queued play starts only when ready',async({page})=>{
  const from=28148,to=38375,revision=`sha256:${'a'.repeat(64)}`
  const target=Date.UTC(2025,8,20,16),timestamp=(n:number)=>target+(n-from)*300000
  await page.addInitScript(({revision,to})=>Object.assign(window,{simulationFixtureProps:{dataset:{dataset_id:'TEST',data_revision:revision,instrument:{symbol:'AO'},timeframe:'5m',source:{timestamp_semantics:'bar_end'},price:{price_scale:1,price_decimals:0},time:{timezone:'Asia/Shanghai'},coverage:{first_bar_index:0,last_bar_index:to}},indicatorSources:[],strategySources:[],replayCursor:null,replayObjects:null,replaySignals:[]}}),{revision,to})
  let readyAt=Infinity,cursor=from,job='create',commands=0
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  const snapshot=()=>({session_id:'preparing',operation_id:job,dataset_id:'TEST',data_revision:revision,run_id:'preparing',run_signature:'hash',from_bar_index:from,to_bar_index:to,cursor,processed_until_bar_index:cursor,state:'live',reset:commands===0,events:[],account:{timestamp_utc:timestamp(cursor),equity_i64:100000000,available_i64:100000000,cash_i64:100000000,margin_i64:0}})
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url())
    if(url.pathname.endsWith('/algorithms'))return route.fulfill({json:{algorithms:[{kind:'strategy',algorithm_id:'divergence_segment_end_simulation',algorithm_version:'1.2.0',source_hash:`sha256:${'b'.repeat(64)}`} ]}})
    if(url.pathname.endsWith('/bars')){
      const before=url.searchParams.get('before_bar_index'),after=url.searchParams.get('after_bar_index'),around=Number(url.searchParams.get('around_bar_index')??from)
      const limit=Number(url.searchParams.get('limit')??3000)
      const start=before?Math.max(0,Number(before)-limit):after?Number(after)+1:Math.max(0,around-1500)
      const end=Math.min(to,before?Number(before)-1:start+limit-1)
      const indices=Array.from({length:Math.max(0,end-start+1)},(_,i)=>start+i)
      return route.fulfill({json:{dataset_id:'TEST',data_revision:revision,generation_id:url.searchParams.get('generation_id'),checksum:`sha256:${'c'.repeat(64)}`,price_scale:1,has_more_before:start>0,has_more_after:end<to,coverage:{first_bar_index:start,last_bar_index:end},bars:{bar_index:indices,timestamp_utc:indices.map(timestamp),open_i64:indices.map(()=>3000),high_i64:indices.map(()=>3002),low_i64:indices.map(()=>2998),close_i64:indices.map(()=>3001),volume:indices.map(()=>100),open_interest:indices.map(()=>null)}}})
    }
    if(url.pathname.endsWith('/training-sessions')){
      expect(route.request().postDataJSON().backtest.range.from_bar_index).toBe(from)
      readyAt=Date.now()+4000
      return route.fulfill({status:202,json:{session_id:'preparing',job_id:job,status:'queued'}})
    }
    if(url.pathname.endsWith('/commands')){
      expect(Date.now()).toBeGreaterThanOrEqual(readyAt)
      const command=route.request().postDataJSON()
      if(command.action==='advance'){commands++;cursor+=command.count}
      job=command.operation_id
      return route.fulfill({status:202,json:{session_id:'preparing',job_id:job,status:'queued'}})
    }
    if(url.pathname.endsWith('/preparing'))return route.fulfill({json:{session_id:'preparing',job_id:job,status:Date.now()<readyAt?'running':'completed',snapshot:Date.now()<readyAt?null:snapshot()}})
    return route.fulfill({json:{}})
  })
  await page.goto('/e2e/divergence-simulation.html')
  const panel=page.getByLabel('三类背驰自动交易模拟'),controls=page.getByLabel('K线模拟速度控制')
  await panel.getByLabel('复盘训练日期').fill('2025-09-21')
  await panel.getByRole('button',{name:'更换至此日期',exact:true}).click()
  await expect(controls).toContainText('K28148 / 38375')
  await controls.getByRole('button',{name:'准备完成后播放',exact:true}).click()
  await expect(controls.getByRole('button',{name:'取消待播放',exact:true})).toBeEnabled()
  await expect(controls.getByRole('status')).toContainText('已等待 1 秒')
  expect(commands).toBe(0)
  await expect(controls.getByRole('button',{name:'暂停模拟',exact:true})).toBeEnabled()
  await expect.poll(()=>cursor).toBeGreaterThan(from)
  await controls.getByRole('button',{name:'暂停模拟',exact:true}).click()
  await page.screenshot({path:'../trading-data/acceptance/strategy-training-preparation.png',fullPage:true})
  expect(errors).toEqual([])
})
