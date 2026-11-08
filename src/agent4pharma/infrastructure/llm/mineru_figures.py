"""从MinerU已解析图块导出原图，不重新解析、不接受模型提供的本地路径。"""

import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

from pydantic import JsonValue

from agent4pharma.infrastructure.llm.mineru_identity import resolve_mineru_identity
from agent4pharma.modules.materials.document_reading import DocumentReadResult, VerifiedDocument
from agent4pharma.modules.publishing.article_layout import FigureImage, FigureSelection


def read_mineru_figure(
    executable: Path | None, document: VerifiedDocument, selection: FigureSelection, scratch: Path
) -> FigureImage:
    """限时导出真实PNG图块并校验来源哈希；技术失败直接上报，不生成占位图。"""
    if executable is None:
        raise ValueError("MinerU未安装，不能提取论文原图")
    if selection.page > document.pages:
        raise ValueError("所选图片页码超出绑定论文")
    identity = resolve_mineru_identity(executable, document.sha256)
    if isinstance(identity, DocumentReadResult):
        raise ValueError(f"原图缓存身份不可用：{identity.error_code}")
    locator = f"doc:{identity.short_id}/tier:standard/page:{selection.page}/block:{selection.block}"
    page = _invoke(executable, ["read", locator.rsplit("/block:", 1)[0], "--limit", "60000", "--json"])
    if page.get("sha256") != document.sha256:
        raise ValueError("原图目录的文档身份不符")
    if not re.search(r"!\[[^\]]*\]\(" + re.escape(locator) + r"\)", str(page.get("content", ""))):
        raise ValueError("所选位置不是已解析的论文图块")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="figure-", dir=scratch) as directory:
        output = Path(directory) / "figure.png"
        payload = _invoke(
            executable, ["read", locator, "--format", "image", "--output", str(output), "--json"]
        )
        if payload.get("sha256") != document.sha256 or not output.is_file():
            raise ValueError("原图导出失败或文档身份不符")
        if output.stat().st_size > 3_000_000:
            raise ValueError("单图超过3MB，请选择更小的原图面板")
        data = output.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("原图导出不是PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= 15000 and 1 <= height <= 15000):
        raise ValueError("原图尺寸无效")
    return FigureImage(data, width, height, locator)


def _invoke(executable: Path, arguments: list[str]) -> dict[str, JsonValue]:
    try:
        result = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            check=False,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ValueError("MinerU原图读取失败或超时") from error
    if result.returncode or not isinstance(payload, dict) or payload.get("error_code"):
        raise ValueError("MinerU原图缓存不可用")
    return payload
