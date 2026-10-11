import type { ChanCalculationResults, ChanTreeObject } from '../types/api'

/** Translate Python evidence; never infer level, completion or a parent core. */
export function recursiveTreeObjects(objects: ChanCalculationResults['objects']): ChanTreeObject[] {
  const names = { consolidation: '盘整', uptrend: '上涨趋势', downtrend: '下跌趋势', unknown: '待分类' }
  return [
    ...(objects.recursive_movements ?? []).map((movement): ChanTreeObject => ({
      object_id: movement.object_id, object_type: 'recursive_movement', layer_category: 'movement_states',
      bar_index: movement.end_bar_index, time: movement.end_time, price_i64: movement.end_price_i64,
      confirmed_at_bar_index: movement.status === 'completed' ? movement.known_at_bar_index : null,
      known_at_bar_index: movement.known_at_bar_index, object_revision: movement.object_revision,
      label: `L${movement.level} ${names[movement.movement_type]}${movement.construction_kind === 'association' ? '结合分析' : ''} · ${movement.status === 'completed' ? '已完成' : movement.status === 'invalidated' ? '已失效' : '形成中'}`,
      detail: `组成 ${movement.component_refs.length} 段 · 中枢 ${movement.center_ids.length} 个 · ${movement.construction_kind === 'association' ? '引用同级块的结合证明，不进入父层' : movement.status === 'completed' ? '同级盘整块已构成' : '等待完整次级走势构成'} · 背驰另行确认`,
    })),
    ...(objects.recursive_centers ?? []).map((center): ChanTreeObject => ({
      object_id: center.object_id, object_type: 'recursive_center', layer_category: 'segment_centers',
      bar_index: center.end_bar_index, time: center.end_time, price_i64: center.zg_i64,
      confirmed_at_bar_index: center.formed_at_bar_index,
      known_at_bar_index: center.known_at_bar_index, object_revision: center.object_revision,
      label: `L${center.level} 递归中枢 · ${center.status === 'closed' ? '固定块已构成' : '形成中'}`,
      detail: `ZD ${center.zd_i64} / ZG ${center.zg_i64} · 三个构成来源 ${center.seed_refs.map((ref) => ref.object_id).join(' → ')} · 形成确认 K${center.formed_at_bar_index}${center.higher_level_review_required ? ' · 外围重叠，等待独立父层构造' : ''}`,
    })),
  ]
}
