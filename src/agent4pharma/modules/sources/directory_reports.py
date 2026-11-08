"""来源目录展示：将已查询的真实记录导出为 CSV 和 Markdown，不直接读取数据库。"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from agent4pharma.shared.serialization import dump


def write_import_report(store, summary: dict) -> Path:
    """将已导入目录的实际投影导出为 JSON、CSV 和 Markdown；不改变目录内容。"""
    root = store.root / "reports"
    root.mkdir(parents=True, exist_ok=True)
    (root / "biotender-venue-diff.json").write_text(dump(summary), encoding="utf-8")
    all_entries = store.list_catalog(limit=20000)
    counts = {
        r["venue_id"]: r["count"]
        for r in [*summary["overlap_with_baseline"], *summary["additions_to_baseline"]]
    }
    _write_catalog_csv(root, all_entries, counts)
    lines = _venue_diff_lines(summary, all_entries)
    destination = root / "biotender-venue-diff.md"
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def write_demo_review(store, result: dict) -> Path:
    """Human-readable relational projection of the actual database, not the seed file."""

    latest = {r["material_id"]: r["metadata"] for r in result["records"]}
    lines = [
        "# 共享数据源 demo：数据库实际记录",
        "",
        "日期：2026-10-29。数据集为 demo；所有新访问入口均未启用定时采集。",
        "",
        f"机构 {result['organization_count']}；来源 {result['source_count']}；"
        f"入口 {result['endpoint_count']}；资料 {result['material_count']}；"
        f"来源记录 {result['record_count']}。",
        "",
        "## 资料与发布者",
        "",
        "cached_detail_and_html = 已读取 BioTender 详情与页面；linked_target_not_fetched = "
        "仅定位到引用链接，未读取目标；http_403 = 正文访问受限；read_via_web_tool = 已经网页工具读取。",
        "",
        "| 类型 | 资料 | 发布机构 | 访问状态 | 引用数 |",
        "|---|---|---|---|---:|",
    ]
    for item in result["materials"]:
        meta = latest[item["id"]]
        lines.append(
            f"| {_cell(item['material_type'])} | [{_cell(item['title'])}]({item['canonical_url']}) | "
            f"{_cell(item['publisher'])} | {_cell(meta['access_state'])} | "
            f"{len(meta.get('references', []))} |"
        )
    lines += [
        "",
        "## 文章到引用材料的追溯",
        "",
        "下列关系仅表示文章引用了该链接，角色尚未核验，不自动认定原始来源。",
        "",
    ]
    for item in result["materials"]:
        refs = latest[item["id"]].get("references", [])
        if not refs:
            continue
        lines += [f"### {_cell(item['title'])}", ""]
        lines += [f"- [{_cell(r['label'] or r['url'])}]({r['url']})：cites / unassessed" for r in refs]
        lines.append("")
    lines += [
        "## 来源与采集入口",
        "",
        "主页候选入口不表示已验证其列表发现能力。NeurIPS 本届主页已读取，但尚未接入论文列表。",
        "",
        "| 来源 | 类型 | 归属机构 | 入口 | 方式 | 能力／状态 | 启用 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(_endpoint_lines(store))
    path = store.root / "reports" / "source-demo-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_catalog_csv(root: Path, all_entries: list[dict], counts: dict[str, int]) -> None:
    with (root / "venue-catalog.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "id",
                "name",
                "kind",
                "watch_status",
                "biotender_record_count",
                "jif",
                "metric_year",
                "metric_state",
                "metric_source",
                "scope_note",
            ]
        )
        for entry in all_entries:
            metric = entry["metric"] or {}
            # Neutralize spreadsheet formula prefixes in untrusted external labels.
            name = entry["name"]
            if name.startswith(("=", "+", "-", "@", "\t", "\r")):
                name = "'" + name
            writer.writerow(
                [
                    entry["id"],
                    name,
                    entry["kind"],
                    entry["watch_status"],
                    counts.get(entry["id"], 0),
                    metric.get("value", ""),
                    metric.get("metric_year", ""),
                    metric.get("verification_state", "unverified"),
                    metric.get("source_url", ""),
                    entry["scope_note"],
                ]
            )


def _venue_diff_lines(summary: dict, all_entries: list[dict]) -> list[str]:
    focus = [r for r in summary["additions_to_baseline"] if r["curated"] and r["kind"] == "journal"]
    lines = [
        "# BioTender 期刊目录差异与数据库导入",
        "",
        f"数据库：`{summary['database']}`",
        "",
        f"扫描记录：{summary['biotender_records_scanned']}；"
        f"非空载体名称：{summary['biotender_raw_venue_labels']}；"
        f"缺载体记录：{summary['biotender_missing_venue']}。",
        "",
        "对照基线是此前文档中的 28 本期刊，不是此前已存在的期刊数据库。",
        "",
        "## 额外整理的期刊",
        "",
        "| 期刊 | BioTender 记录条数 | 指标状态 |",
        "|---|---:|---|",
    ]
    metrics = {e["id"]: e["metric"] for e in all_entries}
    for item in focus:
        metric = metrics[item["venue_id"]]
        state = f"{metric['value']} / {metric['metric_year'] or '年度待核实'}" if metric else "IF 待核实"
        lines.append(f"| {item['name']} | {item['count']} | {state} |")
    lines += ["", "## 原 28 本中已观察到的期刊", "", "| 期刊 | BioTender 记录条数 |", "|---|---:|"]
    lines += [f"| {item['name']} | {item['count']} |" for item in summary["overlap_with_baseline"]]
    lines += [
        "",
        "## 导入边界",
        "",
        "新增关注目录、别名、指标观察和出处；没有导入外部论文全文，也没有启用 IF 硬过滤。",
        "观察记录数可能含跨来源重复，不是独立研究成果数；"
        "Science、Nature、Nature Reviews Drug Discovery 均在关注目录。",
        "未分类载体不自动认定为期刊；预印本平台和会议不赋 IF；未知指标保持空值。",
        f"外部数据生成时间：{summary['snapshot_generated_at']}。",
        "完整载体目录见同目录 venue-catalog.csv，机器差异见 biotender-venue-diff.json。",
    ]
    return lines


def _endpoint_lines(store) -> list[str]:
    lines = []
    rows = store.demo_endpoints()
    for row in rows:
        meta = json.loads(row["metadata"])
        lines.append(
            f"| {_cell(row['name'])} | {_cell(row['source_kind'])} | {_cell(row['organization'])} | "
            f"[入口]({row['url']}) | {_cell(row['access_method'])} | "
            f"{_cell(meta.get('capability'))} / {_cell(meta['access_state'])} | {row['enabled']} |"
        )
    return lines


def _cell(value: object) -> str:
    return html.escape(str(value or "—")).replace("|", "&#124;").replace("\n", " ")
