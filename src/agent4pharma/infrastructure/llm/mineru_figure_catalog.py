"""从MinerU缓存一次建立图块索引；图像按需导出并按文档指纹复用。"""

import json
import re
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent4pharma.infrastructure.llm.figure_contact_sheet import compose_figure_contact_sheet
from agent4pharma.infrastructure.llm.mineru_identity import resolve_mineru_identity
from agent4pharma.infrastructure.llm.mineru_runtime import mineru_environment
from agent4pharma.modules.materials.document_reading import DocumentReadResult, VerifiedDocument
from agent4pharma.modules.publishing.article_layout import FigureImage
from agent4pharma.modules.publishing.figure_catalog import FigureCandidate, FigureCatalog, FigurePage

IMAGE = re.compile(r"!\[[^\]]*\]\((doc:[^)/]+/tier:standard/page:(\d+)/block:(\d+))\)")


def build_figure_catalog(
    executable: Path | None, document: VerifiedDocument, cache: Path, home: Path | None = None
) -> FigureCatalog:
    """仅扫描已解析缓存，不重新解析或上传；截断时失败，不保存不完整目录。"""
    target = cache / document.sha256 / "catalog-v2.json"
    if target.is_file():
        saved = FigureCatalog.model_validate_json(target.read_text("utf-8"))
        if saved.document_sha256 != document.sha256:
            raise ValueError("图目录文档指纹不符")
        return saved
    short = _identity(executable, document, home)
    payload = _run(
        executable, ["read", f"doc:{short}/tier:standard/page:all", "--limit", "1000000", "--json"], home
    )
    if payload.get("sha256") != document.sha256 or payload.get("truncated") or payload.get("next_request"):
        raise ValueError("图目录缓存不完整或身份不符")
    catalog = catalog_from_markdown(str(payload.get("content", "")), document.sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        stream.write(catalog.model_dump_json())
        temporary = Path(stream.name)
    temporary.replace(target)
    return catalog


def catalog_from_markdown(markdown: str, sha256: str) -> FigureCatalog:
    """提取图定位和局部文字；跨页图注保留为候选背景，不凭OCR顺序猜面板标签。"""
    chunks = {
        int(m[1]): m[2]
        for m in re.finditer(r"<!-- page (\d+) of \d+ -->(.*?)(?=<!-- page |\Z)", markdown, re.S)
    }
    pages = []
    for page, content in chunks.items():
        candidates = []
        for match in IMAGE.finditer(content):
            block = int(match[3])
            around = IMAGE.sub(" ", content[max(0, match.start() - 180) : match.end() + 280])
            candidates.append(
                FigureCandidate(
                    figure_id=f"p{page}b{block}",
                    page=page,
                    block=block,
                    nearby_text=re.sub(r"\s+", " ", around).strip()[:450],
                )
            )
        if not candidates:
            continue
        contexts = []
        for neighbor in (page, page - 1, page + 1):
            text = chunks.get(neighbor, "")
            for match in re.finditer(r"(?:\*\*)?Fig\.\s*\d+\s*\|", text):
                contexts.append(re.sub(r"\s+", " ", text[match.start() : match.start() + 2200]))
        pages.append(
            FigurePage(
                page=page,
                overview_id=f"p{page}",
                candidates=candidates,
                caption_context=" ".join(contexts)[:4500],
            )
        )
    first = IMAGE.search(markdown)
    short_id = first[1].split(":", 1)[1].split("/", 1)[0] if first else ""
    return FigureCatalog(document_sha256=sha256, short_id=short_id, pages=pages)


def inspect_cached_figure(
    executable: Path | None, document: VerifiedDocument, figure_id: str, cache: Path, home: Path | None = None
) -> FigureImage:
    """只导出目录存在的页或图块；图片按PDF指纹缓存，重复制作不再调用MinerU导出。"""
    catalog = build_figure_catalog(executable, document, cache, home)
    allowed = {p.overview_id for p in catalog.pages} | {
        c.figure_id for p in catalog.pages for c in p.candidates
    }
    if figure_id not in allowed:
        raise ValueError("图片ID不在绑定论文图目录中")
    match = re.fullmatch(r"p(\d+)(?:b(\d+))?", figure_id)
    short = catalog.short_id
    locator = f"doc:{short}/tier:standard/page:{match[1]}"
    if match[2]:
        locator += f"/block:{match[2]}"
    if not match[2]:
        return _contact_sheet(executable, document, cache, catalog, int(match[1]), home)
    target = cache / document.sha256 / f"{figure_id}.png"
    if not target.is_file():
        with tempfile.TemporaryDirectory(dir=target.parent) as directory:
            output = Path(directory) / "image.png"
            result = _run(
                executable, ["read", locator, "--format", "image", "--output", str(output), "--json"], home
            )
            if result.get("sha256") != document.sha256 or not output.is_file():
                raise ValueError("图片导出身份不符或文件缺失")
            _png(output)
            output.replace(target)
    data, width, height = _png(target)
    return FigureImage(data, width, height, locator)


def _png(path: Path) -> tuple[bytes, int, int]:
    if path.stat().st_size > 8_000_000:
        raise ValueError("图片超过8MB查看上限")
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("缓存图片不是PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not 1 <= width <= 15000 or not 1 <= height <= 15000:
        raise ValueError("图片尺寸无效")
    return data, width, height


def _identity(executable: Path | None, document: VerifiedDocument, home: Path | None) -> str:
    if executable is None:
        raise ValueError("MinerU不可用，未重新解析或切换工具")
    identity = resolve_mineru_identity(executable, document.sha256, home)
    if isinstance(identity, DocumentReadResult):
        raise ValueError("论文图片缓存不可用：" + identity.error_code)
    return identity.short_id


def _run(executable: Path, args: list[str], home: Path | None) -> dict[str, object]:
    try:
        result = subprocess.run(
            [str(executable), *args],
            capture_output=True,
            encoding="utf-8",
            timeout=45,
            env=mineru_environment(home),
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ValueError("图缓存读取失败或超时") from error
    if result.returncode or not isinstance(payload, dict) or payload.get("error_code"):
        raise ValueError("图缓存读取失败")
    return payload


def _contact_sheet(
    executable: Path | None,
    document: VerifiedDocument,
    cache: Path,
    catalog: FigureCatalog,
    page: int,
    home: Path | None,
) -> FigureImage:
    target = cache / document.sha256 / f"p{page}-contact-v1.png"
    locator = f"figure-index:{document.sha256}/page:{page}"
    if not target.is_file():
        candidates = next(p.candidates for p in catalog.pages if p.page == page)
        if len(candidates) > 24:
            raise ValueError("单页图块超过24个，请根据目录直接选择子图")

        def read(candidate: FigureCandidate) -> tuple[str, FigureImage]:
            return candidate.figure_id, inspect_cached_figure(
                executable, document, candidate.figure_id, cache, home
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            images = list(executor.map(read, candidates))
        sheet = compose_figure_contact_sheet(images, locator)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            stream.write(sheet.png)
            temporary = Path(stream.name)
        temporary.replace(target)
    data, width, height = _png(target)
    return FigureImage(data, width, height, locator)
