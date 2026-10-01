import { describe, expect, it } from 'vitest'
import { ChanPrimitive, buildChanGeometry, chanSignalLabel } from './chanPrimitive'
import type { ChanCalculationResults } from '../types/api'

function objects(count: number): ChanCalculationResults['objects'] {
  return {
    processed_bars: [],
    fractals: Array.from({ length: count }, (_, index) => ({
      object_id: `fractal-${index}`, bar_index: index, time: index * 60_000, price_i64: 1000 + index,
      zone_low_i64: 990 + index, zone_high_i64: 1010 + index,
      extreme_source_bar_index: index,
      fractal_type: index % 2 ? 'top' as const : 'bottom' as const, confirmed: index % 3 !== 0,
      status: index % 3 !== 0 ? 'confirmed' as const : 'candidate' as const, invalidation_reason: null,
      aux_strength: 'unclassified' as const, strength_reason: 'lesson_82_numeric_profile_not_matched',
      body_i64: 10, upper_shadow_i64: 5, lower_shadow_i64: 5, range_i64: 20,
      close_position_milli: 500, feature_profile: 'processed_bar_ohlc_v1' as const,
      catalog_algorithm_id: 'ALG-GEO-002' as const, strength_semantic_namespace: 'auxiliary' as const,
      standard_signal: false as const, execution_allowed: false as const,
      confirmed_at_bar_index: index + 2, known_at_bar_index: index + 2, object_revision: 1,
    })),
    bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
  }
}

describe('ChanPrimitive', () => {
  it('uses one primitive with bottom and normal batch views', () => {
    const primitive = new ChanPrimitive()
    expect(primitive.paneViews().map((view) => view.zOrder?.())).toEqual(['bottom', 'normal'])
  })

  it('hit-tests overlapping standard and class trade markers as separate selectable objects', () => {
    const primitive = new ChanPrimitive()
    const source = objects(0)
    source.trade_points = [
      { object_id: 'standard', signal_type: 'buy_1', time: 60_000, price_i64: 1000, status: 'confirmed' },
      { object_id: 'class', signal_type: 'class_buy_1', time: 60_000, price_i64: 1000, status: 'confirmed' },
    ] as never
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (time: number) => time }) },
      series: { priceToCoordinate: (price: number) => price }, requestUpdate: () => {},
    } as never)
    primitive.setData(source, 10)
    expect(primitive.hitTest(60, 100)?.externalId).toBe('trade-point:standard')
    expect(primitive.hitTest(84, 100)?.externalId).toBe('trade-point:class')
    expect(primitive.signalForHit('trade-point:class')?.signal_type).toBe('class_buy_1')
  })

  it('selects a divergence triangle through its original signal ID', () => {
    const primitive = new ChanPrimitive()
    const source = objects(0)
    source.divergences = [{ object_id: 'divergence-1', signal_type: 'bottom_divergence', time: 60_000,
      price_i64: 1000, status: 'confirmed', comparison_reference_object_id: 'segment-a',
      comparison_current_object_id: 'segment-b' }] as never
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (time: number) => time }) },
      series: { priceToCoordinate: (price: number) => price }, requestUpdate: () => {},
    } as never)
    primitive.setData(source, 10)
    expect(primitive.hitTest(36, 100)?.externalId).toBe('divergence:divergence-1')
    expect(primitive.signalForHit('divergence:divergence-1')).toMatchObject({
      comparison_reference_object_id: 'segment-a', comparison_current_object_id: 'segment-b',
    })
  })

  it('keeps invalidated divergence history out of the live chart', () => {
    const source = objects(0)
    source.divergences = [{ object_id: 'revised-away', signal_type: 'top_divergence',
      divergence_kind: 'trend', status: 'invalidated', time: 60_000, price_i64: 1000 }] as never
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(geometry.divergences).toHaveLength(0)
  })

  it('keeps a forming divergence visible and selectable without treating it as confirmed', () => {
    const primitive = new ChanPrimitive()
    const source = objects(0)
    source.divergences = [{ object_id: 'forming-c', signal_type: 'top_divergence',
      divergence_kind: 'trend', divergence_profile: 'segment_trend_candidate',
      status: 'forming', time: 60_000, price_i64: 1000, confirmed: false }] as never
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(geometry.divergences.map((point) => point.status)).toEqual(['forming'])
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (time: number) => time }) },
      series: { priceToCoordinate: (price: number) => price }, requestUpdate: () => {},
    } as never)
    primitive.setData(source, 10)
    expect(primitive.hitTest(36, 100)?.externalId).toBe('divergence:forming-c')
  })

  it('projects 10,000 semantic objects as one batch without Vue nodes', () => {
    const source = objects(10_000)
    const started = performance.now()
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    const elapsed = performance.now() - started
    expect(geometry.fractals).toHaveLength(10_000)
    expect(elapsed).toBeLessThan(250)
  })

  it('drops objects outside the current coordinate projection', () => {
    const geometry = buildChanGeometry(objects(3), 10, (time) => Number(time) === 0 ? null : Number(time), (price) => price)
    expect(geometry.fractals.map((item) => item.x)).toEqual([60, 120])
  })

  it('projects processed bars and the current online bi state', () => {
    const source = objects(0)
    source.processed_bars.push({
      object_id: 'processed-bar-0', normalized_index: 0, start_bar_index: 0, start_time: 60_000,
      end_bar_index: 1, end_time: 120_000, open_i64: 100, high_i64: 120, low_i64: 90,
      close_i64: 110, high_source_bar_index: 1, low_source_bar_index: 0, direction: 'up',
      source_bar_indices: [0, 1], status: 'forming', sealed_at_bar_index: null,
      catalog_event: 'processed_bar_revision', known_at_bar_index: 1, object_revision: 2,
    })
    source.bi_states.push({
      object_id: 'bi-state-current', bar_index: 1, time: 120_000, price_i64: 110,
      state: 'TOP_FORMING', direction: 'up', anchor_fractal_id: 'fractal-0', candidate_object_id: 'bi-0',
      trigger: 'candidate_started', catalog_algorithm_id: 'ALG-GEO-003', known_at_bar_index: 1, object_revision: 2,
    })
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(geometry.processedBars[0]).toMatchObject({ left: 60, right: 120, top: 12, bottom: 9, status: 'forming' })
    expect(geometry.biStates[0]).toMatchObject({ x: 120, y: 11, state: 'TOP_FORMING' })
  })

  it('projects divergence, buy-sell markers and derived structures', () => {
    const source = objects(0)
    source.divergences.push({
      object_id: 'divergence-1', bar_index: 4, time: 240_000, price_i64: 90,
      signal_type: 'bottom_divergence', divergence_kind: 'trend', signal_class: null, strength: null, reference_object_id: 'segment-center-1',
      macd_area_reference: 20, macd_area_current: 10, status: 'confirmed', invalidation_reason: null,
      level_id: 'L0', lower_level_turn_object_id: null, catalog_event: null, catalog_algorithm_id: null, confirmed: true,
      evidence_profile: 'chan108_single_scope_v1', comparison_reference_object_id: 'segment-1',
      comparison_current_object_id: 'segment-3', comparison_rule: 'macd_same_direction_area_contraction_with_new_extreme',
      new_extreme_satisfied: true, departure_object_id: null, return_object_id: null, return_ordinal: null,
      boundary_profile: null, boundary_relation: null, return_depth_to_core_i64: null, return_depth_to_outer_i64: null,
      follow_through_object_id: 'segment-4', follow_through_status: 'observed', confirmation_latency_bars: 0,
      reference_center_ordinal: 1, older_center_count: 0,
      center_chain_profile: 'confirmed_same_level_centers_known_at_signal_v1',
      confirmed_at_bar_index: 4, known_at_bar_index: 4, object_revision: 1,
    })
    source.trade_points.push({
      ...source.divergences[0]!, object_id: 'point-1', signal_type: 'buy_3', divergence_kind: null, signal_class: 'standard', strength: null,
      macd_area_reference: null, macd_area_current: null,
    })
    source.trade_points.push({
      ...source.trade_points[0]!, object_id: 'point-candidate', signal_type: 'buy_1',
      status: 'candidate', confirmed: false, confirmed_at_bar_index: null,
      catalog_event: 'B1_candidate', catalog_algorithm_id: 'ALG-SIG-001',
    })
    source.trade_points.push({
      ...source.trade_points[1]!, object_id: 'point-invalidated', status: 'invalidated',
      invalidation_reason: 'trend_structure_revised', catalog_event: 'B1_invalidated',
    })
    source.movement_states.push({
      object_id: 'state-1', start_bar_index: 1, start_time: 60_000, end_bar_index: 3,
      end_time: 180_000, price_i64: 110, state_type: 'centre_oscillation', direction: null,
      analysis_level: 'segment', reference_object_id: 'segment-center-1', confirmed: true,
      confirmed_at_bar_index: 3, known_at_bar_index: 3, object_revision: 1,
    })
    source.center_monitors.push({
      object_id: 'monitor-1', bar_index: 3, time: 180_000, z_i64: 110, zn_i64: 115,
      z_twice_i64: 221, zn_twice_i64: 231, core_low_i64: 100, core_high_i64: 121,
      range_high_i64: 131, range_low_i64: 100, component_ordinal: 1, component_direction: 'up',
      relative_position: 'above', oscillation_bias: 'strong', breakout_warning: null,
      catalog_algorithm_id: 'ALG-AUX-004', semantic_namespace: 'auxiliary', evidence_level: 'AUXILIARY',
      level_mapping_profile: 'segment_center_components_v1', standard_signal: false,
      execution_allowed: false, confirms_third_point: false,
      analysis_level: 'segment', reference_object_id: 'segment-center-1', confirmed: true,
      confirmed_at_bar_index: 3, known_at_bar_index: 3, object_revision: 1,
    })
    source.center_monitors.push({
      ...source.center_monitors[0]!, object_id: 'monitor-2', bar_index: 4, time: 240_000,
      zn_i64: 121, zn_twice_i64: 243, range_high_i64: 132, range_low_i64: 111,
      component_ordinal: 2, component_direction: 'down', breakout_warning: 'cross_above_b',
      confirmed_at_bar_index: 4, known_at_bar_index: 4,
    })
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(geometry.divergences[0]).toMatchObject({ x: 240, y: 9, signal_type: 'bottom_divergence' })
    expect(geometry.tradePoints.map((point) => point.status)).toEqual(['confirmed', 'candidate', 'invalidated'])
    expect(geometry.movementStates[0]?.state_type).toBe('centre_oscillation')
    expect(geometry.centerMonitors[0]).toMatchObject({ x: 180, y: 11.55, zY: 11.05, oscillation_bias: 'strong' })
    expect(geometry.centerMonitorCurves).toEqual([{
      start: expect.objectContaining({ x: 180, y: 11.55 }),
      end: expect.objectContaining({ x: 240, y: 12.15 }),
      confirmed: true,
    }])
    expect(geometry.centerMonitorAxes).toEqual([{
      start: { x: 180, y: 11.05 }, end: { x: 240, y: 11.05 }, confirmed: true,
    }])
  })

  it('renders BI and segment center layers together with roles and confirmation marker', () => {
    const source = objects(0)
    source.bi.push(
      { object_id: 'bi-entry', start_bar_index: 0, start_time: 0, start_price_i64: 90, start_extreme_source_bar_index: 0, end_bar_index: 1, end_time: 60_000, end_price_i64: 105, end_extreme_source_bar_index: 1, range_low_i64: 90, range_high_i64: 105, range_low_source_bar_index: 0, range_high_source_bar_index: 1, range_profile: 'endpoint_extrema_v1', direction: 'up', status: 'confirmed', invalidation_reason: null, catalog_algorithm_id: 'ALG-GEO-003', confirmed: true, confirmed_at_bar_index: 1, known_at_bar_index: 1, object_revision: 1 },
      { object_id: 'bi-exit', start_bar_index: 3, start_time: 180_000, start_price_i64: 108, start_extreme_source_bar_index: 3, end_bar_index: 4, end_time: 240_000, end_price_i64: 130, end_extreme_source_bar_index: 4, range_low_i64: 108, range_high_i64: 130, range_low_source_bar_index: 3, range_high_source_bar_index: 4, range_profile: 'endpoint_extrema_v1', direction: 'up', status: 'confirmed', invalidation_reason: null, catalog_algorithm_id: 'ALG-GEO-003', confirmed: true, confirmed_at_bar_index: 4, known_at_bar_index: 4, object_revision: 1 },
      { object_id: 'bi-retest', start_bar_index: 4, start_time: 240_000, start_price_i64: 130, start_extreme_source_bar_index: 4, end_bar_index: 5, end_time: 300_000, end_price_i64: 112, end_extreme_source_bar_index: 5, range_low_i64: 112, range_high_i64: 130, range_low_source_bar_index: 5, range_high_source_bar_index: 4, range_profile: 'endpoint_extrema_v1', direction: 'down', status: 'confirmed', invalidation_reason: null, catalog_algorithm_id: 'ALG-GEO-003', confirmed: true, confirmed_at_bar_index: 5, known_at_bar_index: 5, object_revision: 1 },
    )
    source.local_centers.push({
      object_id: 'center-bi', stream_key: 'test|BI|stroke', rule_version: 'local_center_boundary_v1', unit_kind: 'BI', structural_level: 'stroke', scan_floor: 0,
      seed_ids: ['bi-1', 'bi-2', 'bi-3'], zd_i64: 100, zg_i64: 110, seed_start_bar_index: 1, seed_start_time: 60_000, seed_end_bar_index: 3, seed_end_time: 180_000,
      formed_at_bar_index: 3, body_start_bar_index: 1, body_start_time: 60_000, body_end_bar_index: 3, body_end_time: 180_000,
      observed_start_bar_index: 1, observed_start_time: 60_000, observed_end_bar_index: 5, observed_end_time: 300_000, observed_low_i64: 95, observed_high_i64: 130,
      status: 'CLOSED', pending_exit_id: null, exit_id: 'bi-exit', first_retest_id: 'bi-retest', entry_id: 'bi-entry', local_entry: 'FROM_BELOW', break_direction: 'up', break_confirmed_at_bar_index: 5,
      parent_id: null, left_context_incomplete: false, roles_overlap_seed: false, source_revision: 'revision', known_at_bar_index: 5, object_revision: 1,
    })
    source.local_centers.push({ ...source.local_centers[0]!, object_id: 'center-segment', unit_kind: 'SEGMENT', structural_level: 'segment' })
    source.center_connections.push({ object_id: 'connection-1', stream_key: 'test|BI|stroke', rule_version: 'local_center_boundary_v1', unit_kind: 'BI', structural_level: 'stroke', from_center_id: 'center-bi', to_center_id: null, ordered_unit_ids: ['bi-exit', 'bi-retest'], exit_unit_id: 'bi-exit', entry_unit_id: 'bi-entry', first_retest_id: 'bi-retest', start_bar_index: 3, start_time: 180_000, end_bar_index: 5, end_time: 300_000, confirmed_at_bar_index: 5, roles_overlap_seed: false, source_revision: 'revision', known_at_bar_index: 5, object_revision: 1 })
    source.center_audit_events.push({ object_id: 'audit-1', event_type: 'BREAK_CONFIRMED', center_id: 'center-bi', unit_ids: ['bi-exit', 'bi-retest'], zd_i64: 100, zg_i64: 110, comparison_i64: 112, event_bar_index: 5, event_time: 300_000, rule_version: 'local_center_boundary_v1', source_file: 'python/src/tvbt/chan/local_center.py', source_line: 1, known_at_bar_index: 5, object_revision: 1 })

    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)

    expect(geometry.localCenters).toHaveLength(2)
    expect(geometry.localCenters[0]).toMatchObject({ object_id: 'center-bi', left: 60, right: 180, top: 11, bottom: 10 })
    expect(geometry.centerRoleLines.map((line) => [line.role, line.unitId])).toEqual([['entry', 'bi-entry'], ['exit', 'bi-exit'], ['retest', 'bi-retest']])
    expect(geometry.confirmationMarkers).toEqual([{ x: 300, centerId: 'center-bi', barIndex: 5, unitKind: 'BI' }])

    const confirmationOnly = buildChanGeometry(source, 10, (time) => Number(time), (price) => price, new Map([
      ['center-bi', { center: false, boundaryConfirmation: true }],
      ['center-segment', { center: false, boundaryConfirmation: false }],
    ]))
    expect(confirmationOnly.localCenters).toEqual([])
    expect(confirmationOnly.centerRoleLines).toEqual([])
    expect(confirmationOnly.confirmationMarkers).toEqual([{ x: 300, centerId: 'center-bi', barIndex: 5, unitKind: 'BI' }])

    const centerOnly = buildChanGeometry(source, 10, (time) => Number(time), (price) => price, new Map([
      ['center-bi', { center: true, boundaryConfirmation: false }],
      ['center-segment', { center: true, boundaryConfirmation: false }],
    ]))
    expect(centerOnly.localCenters).toHaveLength(2)
    expect(centerOnly.centerRoleLines).toHaveLength(3)
    expect(centerOnly.confirmationMarkers).toEqual([])
    source.center_connections.push({ ...source.center_connections[0]!, object_id: 'connection-in', from_center_id: 'center-before', to_center_id: 'center-bi' })
    const primitive = new ChanPrimitive()
    primitive.setData(source, 10)
    expect(primitive.hoverDetail('local-center:center-bi')).toContain('前向连接：connection-in；后向连接：connection-1')
    expect(primitive.hoverDetail('local-center:center-bi')).toContain('形成确认：K3；分界确认：K5')
    expect(primitive.hoverDetail('local-center:center-bi')).toContain('扫描起点：单元索引 0')
    source.local_centers[0]!.status = 'PENDING_BREAK'
    source.center_audit_events = [{
      ...source.center_audit_events[0]!, object_id: 'preview-1', event_type: 'PREVIEW_UPDATED',
      preview_state: 'RETEST_TOUCH', preview_confirmed: false, comparison_i64: 110,
    }]
    const previewGeometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(previewGeometry.confirmationMarkers).toEqual([])
    expect(previewGeometry.previewMarkers).toEqual([{ x: 300, y: 11, centerId: 'center-bi', state: 'RETEST_TOUCH' }])
    expect(previewGeometry.localCenters[0]!.right).toBe(180)
    source.local_centers[0]!.status = 'CLOSED'
    expect(buildChanGeometry(source, 10, (time) => Number(time), (price) => price).previewMarkers).toEqual([])
  })

  it('updates semantic object rendering styles as one primitive', () => {
    const primitive = new ChanPrimitive()
    primitive.setStyle({ outputs: {
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      trade_point: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
    } })
    expect(primitive.renderStyle()).toEqual({
      processedBar: { color: '#787b86', line_width: 1, line_style: 'dotted', opacity: 0.7, visible: true },
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      biState: { color: '#26c6da', line_width: 1, line_style: 'dashed', opacity: 0.9, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      movementState: { color: '#ab47bc', line_width: 1, line_style: 'dashed', opacity: 0.9, visible: true },
      centerMonitor: { color: '#26c6da', line_width: 1, line_style: 'dotted', opacity: 0.9, visible: true },
      divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      tradePoint: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      biCenter: { color: '#42a5f5', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
      segmentCenter: { color: '#ffb300', line_width: 2, line_style: 'dashed', opacity: 1, visible: true },
    })
  })
})

describe('chanSignalLabel', () => {
  it('labels divergence, class points, and second-point strength', () => {
    expect(chanSignalLabel({ signal_type: 'bottom_divergence', divergence_kind: 'trend', strength: null })).toBe('趋势底背驰')
    expect(chanSignalLabel({ signal_type: 'top_divergence', divergence_kind: 'consolidation', strength: null })).toBe('盘整顶背驰')
    expect(chanSignalLabel({ signal_type: 'top_divergence', divergence_kind: 'trend', divergence_profile: 'segment_trend_candidate', strength: null })).toBe('线段趋势顶背驰')
    expect(chanSignalLabel({ signal_type: 'bottom_divergence', divergence_kind: 'center_oscillation', strength: null })).toBe('中枢震荡底背驰')
    expect(chanSignalLabel({ signal_type: 'buy_2', divergence_kind: null, strength: 'strongest' })).toBe('最强二买')
    expect(chanSignalLabel({ signal_type: 'class_sell_2', divergence_kind: null, strength: 'weakest' })).toBe('最弱类二卖')
    expect(chanSignalLabel({ signal_type: 'class_buy_3', divergence_kind: null, strength: null })).toBe('类三买')
    expect(chanSignalLabel({ signal_type: 'buy_3', divergence_kind: null, strength: null })).toBe('三买')
  })
})
