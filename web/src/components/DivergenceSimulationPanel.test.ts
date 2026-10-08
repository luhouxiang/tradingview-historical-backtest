import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CausalEvent, DatasetMeta, TrainingCommand, TrainingSnapshot } from '../types/api'
import DivergenceSimulationPanel from './DivergenceSimulationPanel.vue'

const api = vi.hoisted(() => ({ getBars:vi.fn(), cancelJob:vi.fn(), createTrainingSession:vi.fn(), commandTrainingSession:vi.fn(), getTrainingSession:vi.fn(), listAlgorithms:vi.fn() }))
vi.mock('../api/client', () => api)
const dataset = { dataset_id:'TEST', data_revision:`sha256:${'1'.repeat(64)}`, price:{ price_scale:1 }, coverage:{first_bar_index:0,last_bar_index:1000} } as DatasetMeta
let snapshot: TrainingSnapshot, currentJob = '', head = 0, sequence = 0
function account(n:number) {
  return { event_type:'simulation_account', known_at_bar_index:n, timestamp_utc:n*60000,
    position_side:n>=6?'short':n>=3?'long':'flat', position_quantity:n>=3?1:0,
    equity_i64:n>=6?20000:10000, available_i64:n>=6?19000:10000, cash_i64:10000,
    unrealized_pnl_i64:n>=6?-200:0, margin_i64:n>=3?1000:0, net_pnl_i64:n>=6?-400:0,
    realized_net_pnl_i64:n>=6?-100:0, closed_trade_count:n>=6?1:0, win_rate:n>=6?0:null,
    total_commission_i64:n>=6?300:n>=3?100:0,total_slippage_i64:n>=6?300:0,max_drawdown:n>=6?0.004:0 }
}
function facts(from:number,to:number):CausalEvent[] {
  const events:CausalEvent[]=[]
  for(let n=from;n<=to;n++) {
    const actions=n===3?['open_long']:n===6?['close_long','open_short']:[]
    actions.forEach((action,i)=>events.push({event_seq:n*10+i,known_at_bar_index:n,object_type:'chart_event',object_id:`fill-${n}-${i}`,operation:'upsert',object_revision:1,
      payload:{event_type:'simulation_fill',action,quantity:1,bar_index:n,timestamp_utc:n*60000,price_i64:100,commission_i64:100,contract_symbol:'TEST',
        position_after_side:action==='open_long'?'long':action==='open_short'?'short':'flat',position_after_quantity:action.startsWith('open')?1:0,
        closed_gross_pnl_i64:action.startsWith('open')?null:100,closed_net_pnl_i64:action.startsWith('open')?null:-100}}))
    events.push({event_seq:n*10+9,known_at_bar_index:n,object_type:'chart_event',object_id:'SIM-ACCOUNT',operation:'upsert',object_revision:n+1,payload:account(n)})
  }
  return events
}
describe('online strategy training panel',()=>{
  beforeEach(()=>{
    vi.clearAllMocks();head=0;sequence=0;currentJob=''
    api.listAlgorithms.mockResolvedValue([{kind:'strategy',algorithm_id:'divergence_segment_end_simulation',algorithm_version:'1',source_hash:'hash'}])
    api.cancelJob.mockResolvedValue({status:'cancelled'})
    api.createTrainingSession.mockImplementation(async request=>{
      currentJob=`create-${++sequence}`;head=request.backtest.range.from_bar_index
      snapshot={session_id:'sim-1',operation_id:request.operation_id,dataset_id:dataset.dataset_id,data_revision:dataset.data_revision,run_id:'sim-1',run_signature:'hash',
        from_bar_index:head,to_bar_index:1000,cursor:head,processed_until_bar_index:head,state:'live',reset:true,events:facts(head,head),account:account(head)}
      return {session_id:'sim-1',job_id:currentJob,status:'queued'}
    })
    api.commandTrainingSession.mockImplementation(async (request:TrainingCommand)=>{
      currentJob=`command-${++sequence}`
      const previous=snapshot.cursor
      const next=request.action==='seek'?request.target_bar_index!:request.action==='advance'?Math.min(1000,previous+request.count!):previous
      head=Math.max(head,next)
      snapshot={...snapshot,cursor:next,processed_until_bar_index:head,operation_id:request.operation_id,state:request.action==='cancel'?'cancelled':next<head?'lookback':'live',
        reset:request.action!=='advance',events:facts(request.action==='advance'?previous+1:snapshot.from_bar_index,next),account:account(next)}
      return {session_id:'sim-1',job_id:currentJob,status:'queued'}
    })
    api.getTrainingSession.mockImplementation(async ()=>({session_id:'sim-1',job_id:currentJob,status:'completed',snapshot}))
  })
  afterEach(()=>vi.useRealTimers())
  async function start(){const wrapper=mount(DivergenceSimulationPanel,{props:{dataset}});await flushPromises();await wrapper.get('.replay-range button').trigger('click');await flushPromises();return wrapper}
  async function jump(wrapper:Awaited<ReturnType<typeof start>>,n:number){await wrapper.get('[aria-label="模拟跳转K线"]').setValue(n);await wrapper.findAll('.replay-controls button')[1]!.trigger('click');await flushPromises()}
  it('creates only start prefix, advances backend, and views history without creating a new session',async()=>{
    const wrapper=await start()
    expect(head).toBe(0);expect(api.commandTrainingSession).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('当前权益 100.00')
    await jump(wrapper,6)
    expect(api.commandTrainingSession).toHaveBeenLastCalledWith(expect.objectContaining({action:'advance',count:6,expected_cursor:0}))
    expect(wrapper.text()).toContain('已成交 3 次');expect(wrapper.text()).toContain('空仓（做空） 1 手')
    expect(wrapper.findAll('tbody tr')[1]!.text()).toContain('-1.00')
    await jump(wrapper,2)
    expect(api.commandTrainingSession).toHaveBeenLastCalledWith(expect.objectContaining({action:'seek',target_bar_index:2}))
    expect(wrapper.text()).toContain('记录回看');expect(wrapper.text()).toContain('已成交 0 次')
    expect(api.createTrainingSession).toHaveBeenCalledTimes(1);wrapper.unmount()
  })
  it('selected speed advances backend; pause issues no further commands',async()=>{
    vi.useFakeTimers();const wrapper=await start();wrapper.vm.togglePlay()
    await vi.advanceTimersByTimeAsync(1000);expect(snapshot.cursor).toBe(1)
    wrapper.vm.setSpeed(100);await vi.advanceTimersByTimeAsync(1000);expect(snapshot.cursor).toBe(101)
    wrapper.vm.togglePlay();const calls=api.commandTrainingSession.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000);expect(api.commandTrainingSession).toHaveBeenCalledTimes(calls)
    expect(api.commandTrainingSession.mock.calls.every(([r])=>r.count<=100)).toBe(true);wrapper.unmount()
  })
  it('selects an individual fill and focuses its execution bar without seeking or changing account',async()=>{
    const wrapper=await start();await jump(wrapper,6)
    const calls=api.commandTrainingSession.mock.calls.length
    wrapper.vm.togglePlay()
    const row=wrapper.get('[data-fill-id="fill-3-0"]')
    await row.trigger('click')
    expect(row.attributes('aria-selected')).toBe('true')
    expect(row.classes()).toContain('selected')
    await row.trigger('dblclick');await flushPromises()
    expect(wrapper.emitted('focusFill')!.at(-1)![0]).toMatchObject({object_id:'fill-3-0',bar_index:3,timestamp_utc:180000,price_i64:100})
    expect(api.commandTrainingSession).toHaveBeenCalledTimes(calls)
    expect(wrapper.get('[aria-label="模拟跳转K线"]').element).toHaveProperty('value','6')
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    expect(wrapper.text()).toContain('空仓（做空） 1 手')
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({playing:false,cursor:6})
    wrapper.vm.togglePlay()
    expect(wrapper.emitted('focusFill')!.at(-1)![0]).toBeNull()
    wrapper.unmount()
  })
  it('isolates late creation from previous revision',async()=>{
    let resolve!:(value:unknown)=>void
    api.createTrainingSession.mockReturnValue(new Promise(r=>{resolve=r}))
    const wrapper=mount(DivergenceSimulationPanel,{props:{dataset}});await flushPromises();await wrapper.get('.replay-range button').trigger('click')
    await wrapper.setProps({dataset:{...dataset,data_revision:'new'}})
    api.getTrainingSession.mockResolvedValue({job_id:'old-job',status:'cancelled'})
    resolve({session_id:'old',job_id:'old-job'});await flushPromises()
    expect(api.cancelJob).toHaveBeenCalledWith('old-job')
    expect(wrapper.emitted('update')!.at(-1)![0]).toMatchObject({cursor:null});wrapper.unmount()
  })
  it('exit cancels preparation promptly and enables fresh start',async()=>{
    api.getTrainingSession.mockReturnValue(new Promise(()=>undefined))
    const wrapper=await start();expect(wrapper.get('.replay-range button').attributes('disabled')).toBeDefined()
    wrapper.vm.exit();await flushPromises();expect(api.cancelJob).toHaveBeenCalledWith('create-1')
    expect(wrapper.get('.replay-range button').attributes('disabled')).toBeUndefined();wrapper.unmount()
  })
  it('shows selected start during preparation and plays only after an explicit queued request',async()=>{
    vi.useFakeTimers()
    let resolve!:(value:unknown)=>void
    api.getTrainingSession.mockReturnValueOnce(new Promise(r=>{resolve=r}))
    const wrapper=mount(DivergenceSimulationPanel,{props:{dataset}})
    await flushPromises()
    await wrapper.findAll('.replay-range input[type=number]')[0]!.setValue(500)
    await wrapper.get('.replay-range button').trigger('click');await flushPromises()
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({cursor:500,preparing:true,ready:false})
    wrapper.vm.togglePlay()
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({pendingPlay:true,playing:false})
    expect(api.commandTrainingSession).not.toHaveBeenCalled()
    resolve({session_id:'sim-1',job_id:currentJob,status:'completed',snapshot})
    await flushPromises()
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({preparing:false,playing:true})
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.commandTrainingSession).toHaveBeenCalledWith(expect.objectContaining({action:'advance',expected_cursor:500,count:1}))
    wrapper.unmount()
  })
  it('updates elapsed preparation feedback and does not autoplay when queued request is cancelled',async()=>{
    vi.useFakeTimers()
    let completed=false
    api.getTrainingSession.mockImplementation(async()=>({session_id:'sim-1',job_id:currentJob,status:completed?'completed':'running',snapshot:completed?snapshot:null}))
    const wrapper=await start()
    wrapper.vm.togglePlay();wrapper.vm.togglePlay()
    await vi.advanceTimersByTimeAsync(2100)
    expect(wrapper.text()).toContain('已等待 2 秒')
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({preparing:true,pendingPlay:false})
    completed=true
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({preparing:false,ready:true,playing:false})
    expect(api.commandTrainingSession).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('locates natural date via bounded Go queries and creates fresh session',async()=>{
    api.getBars.mockImplementation(async (id,revision,_generation,range)=>{const n=range.beforeBarIndex-1;return {dataset_id:id,data_revision:revision,bars:{bar_index:[n],timestamp_utc:[Date.UTC(2026,7,n<500?7:10)]}}})
    const wrapper=mount(DivergenceSimulationPanel,{props:{dataset}});await flushPromises()
    await wrapper.get('[aria-label="复盘训练日期"]').setValue('2026-08-08')
    await wrapper.findAll('button').find(b=>b.text()==='更换至此日期')!.trigger('click');await flushPromises()
    expect(api.createTrainingSession).toHaveBeenLastCalledWith(expect.objectContaining({backtest:expect.objectContaining({range:{warmup_from_bar_index:0,from_bar_index:500,to_bar_index:1000}})}))
    expect(api.getBars.mock.calls.every(call=>call[3].limit===1)).toBe(true);wrapper.unmount()
  })
  it('report is current-prefix account and resets on lookback',async()=>{
    const wrapper=await start();await wrapper.findAll('button').find(b=>b.text()==='分析报告')!.trigger('click')
    await jump(wrapper,6);expect(wrapper.get('[aria-label="当前复盘训练分析报告"]').text()).toContain('含浮盈总盈亏 -4.00 元')
    await jump(wrapper,2);expect(wrapper.get('[aria-label="当前复盘训练分析报告"]').text()).not.toContain('-4.00');wrapper.unmount()
  })
  it('keeps already executed fills on a resource failure and disables further execution',async()=>{
    const wrapper=await start()
    api.commandTrainingSession.mockImplementation(async (request:TrainingCommand)=>{
      currentJob='failed-command'
      snapshot={...snapshot,operation_id:request.operation_id,cursor:3,processed_until_bar_index:3,state:'interrupted',reset:false,events:facts(1,3),account:account(3)}
      return {session_id:'sim-1',job_id:currentJob,status:'queued'}
    })
    api.getTrainingSession.mockImplementation(async ()=>({session_id:'sim-1',job_id:currentJob,status:'failed',snapshot,error:{code:'RESOURCE_MEMORY_LIMIT',message:'测试内存保护'}}))
    await jump(wrapper,6)
    expect(wrapper.text()).toContain('训练已中断')
    expect(wrapper.text()).toContain('已成交 1 次')
    expect(wrapper.text()).toContain('测试内存保护')
    expect(wrapper.emitted('controls')!.at(-1)![0]).toMatchObject({ready:false})
    await jump(wrapper,8)
    expect(api.commandTrainingSession).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
