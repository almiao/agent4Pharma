"""Render the discovery projection without owning database state."""

from pathlib import Path

from agent4pharma.shared.serialization import dump


def write_report_files(root: Path, report: dict) -> Path:
    """将报告投影写为 JSON/Markdown，返回 JSON 路径；不写数据库状态。"""
    directory = root / "reports"
    directory.mkdir(exist_ok=True)
    path = directory / f"{report['batch_id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(dump(report), encoding="utf-8")
    temporary.replace(path)
    lines = [
        f"# {report['batch_id']}",
        "",
        f"Status: {report['status']}",
        "",
        str(report["screening"]),
        f"Awaiting intelligence: {report.get('awaiting_intelligence', 0)}",
        "",
    ]
    for candidate in report["candidates"]:
        lines.append(f"- {candidate['title']} ({candidate['identity']})")
        if brief := candidate["assessment"].get("brief"):
            lines.append(f"  {brief['summary']}")
    lines += ["", "## Coverage", ""]
    lines += [f"- {j['request']['source']}: {j['status']}; {j['error'] or ''}" for j in report["sources"]]
    path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return path
