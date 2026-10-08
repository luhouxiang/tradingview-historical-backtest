import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SimulationSpeedBar from './SimulationSpeedBar.vue'

describe('simulation preparation controls',()=>{
  it('explains preparation and accepts an explicit deferred play request',async()=>{
    const wrapper=mount(SimulationSpeedBar,{props:{speed:50,ready:false,preparing:true,pendingPlay:false,status:'准备到 K28148 · 已等待 20 秒',playing:false,cursor:28148,to:38375}})
    const play=wrapper.findAll('button')[0]!
    expect(play.text()).toBe('准备完成后播放')
    expect(play.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('K28148 / 38375')
    expect(wrapper.get('[role=status]').text()).toContain('已等待 20 秒')
    expect(wrapper.findAll('button')[1]!.attributes('disabled')).toBeDefined()
    await play.trigger('click');expect(wrapper.emitted('play')).toHaveLength(1)
    await wrapper.setProps({pendingPlay:true})
    expect(play.text()).toBe('取消待播放')
    expect(wrapper.get('[role=status]').text()).toContain('已请求播放')
  })
  it('does not offer deferred play for a failed or interrupted session',()=>{
    const wrapper=mount(SimulationSpeedBar,{props:{speed:50,ready:false,preparing:false,playing:false,cursor:28148,to:38375}})
    expect(wrapper.findAll('button')[0]!.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[role=status]').exists()).toBe(false)
  })
})
