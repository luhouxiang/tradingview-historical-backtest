"""Export read-only, event-time evidence for the documented AOL9 class second sell.

Run with the repository's pinned Python 3.14. No cache/run/source data is modified.
The segment confirmation probe calls the production scanner, not a copied algorithm.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from html import escape
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tvbt.chan import reference
from tvbt.chan.algorithm import definition
from tvbt.chan.engine import ChanEngine
from tvbt.chan.signals import _macd_area


def line(row):
    value = dict(row)
    for side in ("start", "end"):
        value[side] = SimpleNamespace(
            bar_index=row[f"{side}_bar_index"],
            time=row[f"{side}_time"],
            price_i64=row[f"{side}_price_i64"],
        )
    return SimpleNamespace(**value)


def local_time(ms):
    return datetime.fromtimestamp(ms / 1000, timezone(timedelta(hours=8))).isoformat()


def probe(events, bars, at, target):
    state = {}
    for event in events:
        if event["known_at_bar_index"] > at or event["object_type"] != "bi":
            continue
        if event["operation"] == "delete":
            state.pop(event["object_id"], None)
        else:
            state[event["object_id"]] = event["payload"]
    rows = sorted(
        (r for r in state.values() if r.get("status") == "confirmed"),
        key=lambda r: r["start_bar_index"],
    )
    lines = [line(r) for r in rows]
    assert all(a.end.bar_index == b.start.bar_index for a, b in pairwise(lines))
    calls = []
    original = reference._confirm_up_segment

    def tracked(segment, current, inputs, raw):
        relevant = inputs[segment.end_index].start.bar_index == target
        before = asdict(segment)
        result = original(segment, current, inputs, raw)
        if relevant:
            selected = {segment.start_index, segment.end_index, current}
            selected.update(range(before["end_index"] + 1, current + 1))
            calls.append(
                {
                    "before": before,
                    "current_line_index": current,
                    "return_status": result,
                    "lines": [
                        {
                            "line_index": i,
                            **rows[i],
                            "start_raw_high": raw[inputs[i].start.bar_index].high_i64,
                            "start_raw_low": raw[inputs[i].start.bar_index].low_i64,
                        }
                        for i in sorted(selected)
                    ],
                }
            )
        return result

    raw = [SimpleNamespace(**r, time=r["timestamp_utc"]) for r in bars[: at + 1]]
    with patch.object(reference, "_confirm_up_segment", tracked):
        result = reference.reference_segments(lines, raw)
    return {
        "as_of_bar_index": at,
        "confirmed_bi_count": len(lines),
        "target_segments": [asdict(s) for s in result if s.end_bar_index == target],
        "confirmation_calls": calls,
    }


def chart(path, bars, start, end, segments, markers, title, levels=(), center=None):
    """Static OHLC SVG: actual raw candles, linear bar-number axis, no resampling."""
    width, height = 1440, 620
    left, right, top, bottom = 72, 1350, 90, 530
    data = bars[start : end + 1]
    low = min(r["low_i64"] for r in data) - 7
    high = max(r["high_i64"] for r in data) + 10
    if center:
        low = min(low, center[0] - 5)

    def x(i):
        return left + (i - start) / (end - start) * (right - left)

    def y(p):
        return bottom - (p - low) / (high - low) * (bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#111827"/>',
        '<g font-family="Microsoft YaHei, sans-serif" fill="#e5e7eb">',
        f'<text x="30" y="32" font-size="22">{escape(title)}</text>',
        '<text x="30" y="60" font-size="14">真实原始 K 线；横轴 K 序号 = bar_index + 1；'
        "红涨绿跌；粗线为结构端点连线，非当时已知状态</text>",
    ]
    if center:
        parts.append(
            f'<rect x="{left}" y="{y(center[1]):.2f}" width="{right - left}" '
            f'height="{y(center[0]) - y(center[1]):.2f}" fill="#facc15" opacity="0.12"/>'
        )
    for price in range(int(low // 10 * 10), int(high) + 1, 10):
        if low <= price <= high:
            parts.append(f'<path d="M {left} {y(price):.2f} H {right}" stroke="#293548"/>')
            parts.append(f'<text x="1360" y="{y(price) + 5:.2f}" font-size="13">{price}</text>')
    body_width = max(0.45, min(5, (right - left) / (end - start) * 0.7))
    for r in data:
        xx = x(r["bar_index"])
        color = "#fb7185" if r["close_i64"] >= r["open_i64"] else "#2dd4bf"
        parts.append(
            f'<path d="M {xx:.2f} {y(r["high_i64"]):.2f} V {y(r["low_i64"]):.2f}" '
            f'stroke="{color}" stroke-width="0.65"/>'
        )
        body_top = min(y(r["open_i64"]), y(r["close_i64"]))
        body_height = max(0.8, abs(y(r["open_i64"]) - y(r["close_i64"])))
        parts.append(
            f'<rect x="{xx - body_width / 2:.2f}" y="{body_top:.2f}" '
            f'width="{body_width:.2f}" height="{body_height:.2f}" '
            f'fill="{color}"/>'
        )
    for s in segments:
        if start <= s["start_bar_index"] and s["end_bar_index"] <= end:
            parts.append(
                f'<path d="M {x(s["start_bar_index"]):.2f} {y(s["start_price_i64"]):.2f} '
                f'L {x(s["end_bar_index"]):.2f} {y(s["end_price_i64"]):.2f}" '
                'stroke="#60a5fa" stroke-width="2.5" fill="none"/>'
            )
    for price, label in levels:
        parts.append(
            f'<path d="M {left} {y(price):.2f} H {right}" stroke="#a78bfa" stroke-dasharray="7 4"/>'
        )
        parts.append(
            f'<text x="78" y="{y(price) - 6:.2f}" font-size="14" fill="#ddd6fe">'
            f"{escape(label)}</text>"
        )
    for number, price, label, color, row in markers:
        xx, yy = x(number), y(price)
        label_x = max(left, min(right - 260, xx + 8))
        parts.append(
            f'<path d="M {xx:.2f} {top} V {bottom}" stroke="{color}" stroke-dasharray="5 4"/>'
        )
        parts.append(
            f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="5" fill="#111827" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{label_x:.2f}" y="{top + 20 + row * 22}" '
            f'font-size="15" fill="{color}">{escape(label)}</text>'
        )
    for j in range(7):
        i = round(start + (end - start) * j / 6)
        parts.append(
            f'<text x="{x(i):.2f}" y="555" text-anchor="middle" font-size="13">K{i + 1}</text>'
        )
    parts.extend(
        [
            '<text x="30" y="592" font-size="14">数据：AOL9 · 5m · 算法 17.0.0；'
            "确认时刻来自 events.parquet，不以图形端点替代。</text>",
            "</g></svg>",
        ]
    )
    path.write_text("\n".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert sys.version_info[:2] == (3, 14), "Python 3.14 is required"
    target = 70661
    bars = pq.read_table(args.bars).to_pylist()
    manifest = json.loads((args.cache / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["algorithm"]["source_hash"] == definition()["source_hash"]
    assert manifest["algorithm"]["algorithm_version"] == "17.0.0"
    assert manifest["dataset_id"] == "SHFE.AOL9.5m"
    meta = json.loads(args.bars.with_name("meta.json").read_text(encoding="utf-8"))
    assert manifest["data_revision"] == meta["data_revision"]
    tables = {
        name: pq.read_table(args.cache / f"{name}.parquet").to_pylist()
        for name in ("trade_points", "divergences", "segments", "local_centers")
    }
    (point,) = [
        r
        for r in tables["trade_points"]
        if r["bar_index"] == target and r["signal_type"] == "class_sell_2"
    ]
    by_id = {r["object_id"]: r for rows in tables.values() for r in rows}
    origin = by_id[point["reference_object_id"]]
    center = by_id[origin["reference_object_id"]]
    current = by_id[point["comparison_current_object_id"]]
    macd = SimpleNamespace(_macd_fast=None, _macd_slow=None, _macd_dea=None, _macd_histogram={})
    for bar in bars:
        ChanEngine._append_macd(macd, SimpleNamespace(**bar))
    for name in ("reference", "current"):
        measured = _macd_area(
            line(by_id[origin[f"comparison_{name}_object_id"]]), macd._macd_histogram
        )
        assert abs(measured - origin[f"macd_area_{name}"]) < 1e-9
    ids = {
        point["object_id"],
        origin["object_id"],
        center["object_id"],
        current["object_id"],
        origin["comparison_reference_object_id"],
        origin["comparison_current_object_id"],
        *center["seed_ids"],
    }
    related = [
        s
        for s in tables["segments"]
        if center["seed_start_bar_index"] <= s["start_bar_index"] <= current["end_bar_index"]
    ]
    ids.update(s["object_id"] for s in related)
    events = []
    selected_types = ["bi", "segment", "local_center", "divergence", "trade_point", "fractal"]
    for row in pq.read_table(
        args.cache / "events.parquet", filters=[("object_type", "in", selected_types)]
    ).to_pylist():
        row["payload"] = json.loads(row.pop("payload_json"))
        events.append(row)
    events.sort(key=lambda e: e["event_seq"])
    history = [e for e in events if e["object_id"] in ids]
    first_events = {}
    for e in events:
        p = e["payload"]
        if (
            e["operation"] == "upsert"
            and p.get("signal_type") == "class_sell_2"
            and p.get("confirmed")
        ):
            first_events.setdefault(e["object_id"], e)
    first = first_events[point["object_id"]]
    known = first["known_at_bar_index"]
    assert (known, point["price_i64"], bars[known]["close_i64"]) == (70785, 2728, 2716)
    late = []
    for e in first_events.values():
        p, k = e["payload"], e["known_at_bar_index"]
        late.append(
            {
                "object_id": e["object_id"],
                "event_seq": e["event_seq"],
                "bar_index": p["bar_index"],
                "first_known_at": k,
                "signal_price": p["price_i64"],
                "close_at_confirmation": bars[k]["close_i64"],
                "rise_vs_signal": bars[k]["close_i64"] - p["price_i64"],
                "latency": k - p["bar_index"],
                "strength": p["strength"],
            }
        )
    late.sort(key=lambda r: r["rise_vs_signal"], reverse=True)
    assert (late[0]["bar_index"], late[0]["first_known_at"], late[0]["signal_price"]) == (
        25911,
        26079,
        3988,
    )
    assert late[0]["close_at_confirmation"] == 4032
    late_point = first_events[late[0]["object_id"]]["payload"]
    late_segment_id = late_point["comparison_current_object_id"]
    late_ids = {late_point["object_id"], late_point["reference_object_id"], late_segment_id}
    for e in events:
        if (
            e["object_type"] == "divergence"
            and e["payload"].get("comparison_current_object_id") == late_segment_id
        ):
            late_ids.add(e["object_id"])
            if e["payload"].get("follow_through_object_id"):
                late_ids.add(e["payload"]["follow_through_object_id"])
    probes = [probe(events, bars, k, target) for k in (known - 1, known)]
    assert not any(s["confirmed"] for s in probes[0]["target_segments"])
    assert any(s["confirmed"] for s in probes[1]["target_segments"])
    indexes = {target, known - 1, known, known + 1, origin["bar_index"]}
    for s in related:
        indexes.update(
            s[k] for k in ("start_bar_index", "end_bar_index", "range_high_source_bar_index")
        )
    evidence = {
        "manifest": manifest,
        "numbering": "display K = bar_index + 1; timestamps are bar ends (UTC+08)",
        "point": point,
        "origin_divergence": origin,
        "center": center,
        "related_segments": related,
        "history": history,
        "first_publication": first,
        "segment_prefix_probes": probes,
        "confirmation_events": [e for e in events if e["known_at_bar_index"] == known],
        "late_confirmation_population": late,
        "late_example_history": [e for e in events if e["object_id"] in late_ids],
        "key_bars": [
            {**bars[i], "local_bar_end": local_time(bars[i]["timestamp_utc"])}
            for i in sorted(indexes)
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    chart(
        args.output / "overview.svg",
        bars,
        68984,
        70805,
        related,
        [
            (69892, 2746, "来源段完整最高价 K69893 · 2746", "#c4b5fd", 0),
            (70165, 2723, "来源段端点 K70166 · 2723", "#c4b5fd", 1),
            (target, 2728, "类二卖锚点 K70662 · 2728", "#facc15", 2),
            (known, 2716, "首次发布 K70786 · 收2716", "#f472b6", 3),
        ],
        "图1：来源中枢、来源段与类二卖回抽段（全链路）",
        [(2746, "一般二卖比较基准 2746（不是端点 2723）")],
        (2631, 2685),
    )
    chart(
        args.output / "confirmation.svg",
        bars,
        70640,
        70805,
        [],
        [
            (target, 2728, "历史锚点 K70662 · 2728", "#facc15", 0),
            (70729, 2699, "K70730 · 2699", "#60a5fa", 1),
            (70782, 2722, "K70783 · 2722 顶分型", "#60a5fa", 2),
            (known, 2716, "K70786 · 确认发布，收2716", "#f472b6", 3),
        ],
        "图2：端点之后124根，为什么到 K70786 才发布",
        [(2728, "卖点锚价 2728；并非确认时可成交价")],
    )
    chart(
        args.output / "late-rise.svg",
        bars,
        25850,
        26100,
        [],
        [
            (25911, 3988, "K25912 · 卖点锚价3988", "#facc15", 0),
            (26013, bars[26013]["close_i64"], "K26014 · 段的修订确认时刻", "#60a5fa", 1),
            (26079, 4032, "K26080 · 首次发布，收4032", "#f472b6", 2),
        ],
        "图3：确实存在涨过历史卖点后才发布的类二卖",
        [(3988, "历史卖点3988 ≠ 首次发布时的4032")],
    )
    print(
        json.dumps(
            {
                "first_publication": known,
                "prefix_probe_passed": True,
                "late_top": late[:5],
                "count": len(late),
                "risen_count": sum(r["rise_vs_signal"] > 0 for r in late),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
