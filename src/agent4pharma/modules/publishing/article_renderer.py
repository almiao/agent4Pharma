"""无模型、无外部依赖的新闻阅读模板；原图内嵌，微信片段仅使用内联样式。"""

import base64
import hashlib
import html
from collections.abc import Sequence

from agent4pharma.modules.publishing.article_layout import ArticleEmphasis, FigureImage, FigureRecord
from agent4pharma.modules.publishing.paper_header import PaperHeader

TEMPLATE_VERSION = "science-reading-v8"
FONT = '-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif'
CSS = """
*{box-sizing:border-box}html{background:#f0f3f7;color:#243246}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
article{max-width:824px;margin:36px auto;padding:54px 56px 44px;background:white}
header{border-top:4px solid #244b70;padding-top:25px;margin-bottom:32px}
h1{font-size:32px;line-height:1.45;letter-spacing:-.4px;color:#163651;margin:0;font-weight:700}
.prose h2{font-size:22px;line-height:1.5;color:#163651;margin:36px 0 18px;overflow-wrap:anywhere}
.prose p{font-size:18px;line-height:1.95;margin:0 0 24px;overflow-wrap:anywhere}
.prose>p:first-child{font-size:19px;color:#163651}
figure{margin:34px 0;padding:22px 0;border-top:1px solid #e2e8ef;border-bottom:1px solid #e2e8ef}
figure img{display:block;max-width:100%;height:auto;margin:0 auto}
figcaption{margin:18px 0 0;font-size:14px;line-height:1.8;color:#52647a}
figcaption strong{color:#244b70}figcaption small{display:block;margin-top:5px;font-size:12px}
a{color:#244b70;overflow-wrap:anywhere}footer{margin-top:36px;border-top:1px solid #e2e8ef;padding-top:20px}
footer p,footer li{font-size:13px;line-height:1.8;color:#52647a}footer ul{padding-left:20px}
@media(max-width:600px){html{background:#fff}article{margin:0;padding:26px 20px 32px}
h1{font-size:26px;line-height:1.5}.prose p{font-size:17px;line-height:1.95}
.prose>p:first-child{font-size:18px}
figure{margin:28px 0;padding:18px 0}figcaption{font-size:14px}}
@media print{html{background:white}article{margin:0;padding:0}figure{break-inside:avoid}}
"""


def text_hash(value: str) -> str:
    """对准确输出计算指纹，不合并空白或规范化正文。"""
    return hashlib.sha256(value.encode()).hexdigest()


def article_paragraphs(body: str) -> list[str]:
    """与既有文本渠道一致保留每个非空正文行，既不摘要也不改写。"""
    return [line for line in body.splitlines() if line.strip()]


def emphasis_ranges(body: str, emphasis: ArticleEmphasis | None) -> dict[int, list[tuple[int, int]]]:
    """验证准确版本、唯一文字和不重叠位置；不判断重点选择的编辑质量。"""
    ranges: dict[int, list[tuple[int, int]]] = {}
    if emphasis is None:
        return ranges
    if emphasis.source_body_hash != text_hash(body):
        raise ValueError("重点标注不属于当前正文版本")
    paragraphs = article_paragraphs(body)
    for item in emphasis.items:
        if item.paragraph > len(paragraphs):
            raise ValueError("重点标注段落超出正文范围")
        paragraph = paragraphs[item.paragraph - 1]
        if paragraph.startswith("## ") or not item.quote.strip() or paragraph.count(item.quote) != 1:
            raise ValueError("重点标注须为普通段落中唯一的准确文字")
        start = paragraph.index(item.quote)
        end = start + len(item.quote)
        spans = ranges.setdefault(item.paragraph, [])
        if any(start < old_end and end > old_start for old_start, old_end in spans):
            raise ValueError("重点标注不能重复或重叠")
        spans.append((start, end))
    return ranges


def _emphasized_text(paragraph: str, ranges: list[tuple[int, int]]) -> str:
    parts, cursor = [], 0
    for start, end in sorted(ranges):
        parts.append(html.escape(paragraph[cursor:start]))
        parts.append(f'<strong style="font-weight:700;">{html.escape(paragraph[start:end])}</strong>')
        cursor = end
    parts.append(html.escape(paragraph[cursor:]))
    return "".join(parts)


def _source_link(title: str, url: str) -> str:
    label = html.escape(title)
    if url.startswith(("https://", "http://")):
        return f'<a href="{html.escape(url, quote=True)}" rel="noreferrer">{label}</a>'
    return label


def _figure(record: FigureRecord, image: FigureImage, *, wechat: bool) -> str:
    selection = record.selection
    uri = "data:image/png;base64," + base64.b64encode(image.png).decode("ascii")
    img = (
        f'<img src="{uri}" alt="{html.escape(selection.label + "：" + selection.caption, quote=True)}" '
        f'width="{image.width}" height="{image.height}" '
        'style="max-width:100%;height:auto;display:block;margin:0 auto;" />'
    )
    label, caption = html.escape(selection.label), html.escape(selection.caption)
    if wechat:
        return (
            '<section style="margin:28px 0;padding:18px 0;border-top:1px solid #e2e8ef;'
            'border-bottom:1px solid #e2e8ef;">'
            f'{img}<p style="margin:16px 0 6px;font-size:14px;line-height:1.8;color:#52647a;">'
            f'<strong style="color:#244b70;"><span leaf="">{label}</span></strong>'
            f'<br/><span leaf="">{caption}</span></p></section>'
        )
    return f"<figure>{img}<figcaption><strong>{label}</strong><br/>{caption}</figcaption></figure>"


def render_article(
    title: str,
    body: str,
    figures: Sequence[tuple[FigureRecord, FigureImage]],
    sources: Sequence[tuple[str, str]],
    emphasis: ArticleEmphasis | None = None,
    paper_header: PaperHeader | None = None,
) -> tuple[str, str]:
    """生成网页文档及公众号正文片段；数据仅以转义文本或PNG进入模板。"""
    paragraphs = article_paragraphs(body)
    ranges = emphasis_ranges(body, emphasis)
    if any(record.selection.after_paragraph > len(paragraphs) for record, _ in figures):
        raise ValueError("原图插入位置超出定稿段落范围")
    header_after = _header_position(paragraphs)
    web, wechat = [], []
    for index, paragraph in enumerate(paragraphs, 1):
        heading = paragraph.startswith("## ") and bool(paragraph[3:].strip())
        value = (
            html.escape(paragraph[3:].strip())
            if heading
            else _emphasized_text(paragraph, ranges.get(index, []))
        )
        if heading:
            web.append(f"<h2>{value}</h2>")
            wechat.append(
                '<h2 style="margin:32px 0 18px;font-size:21px;line-height:1.5;'
                f'color:#163651;overflow-wrap:anywhere;"><span leaf="">{value}</span></h2>'
            )
        else:
            web.append(f"<p>{value}</p>")
            wechat.append(
                '<p style="margin:0 0 24px;font-size:17px;line-height:1.95;color:#243246;'
                f'text-align:left;overflow-wrap:anywhere;"><span leaf="">{value}</span></p>'
            )
        if index == header_after and paper_header and paper_header.status == "ready":
            header = _paper_header(paper_header)
            web.append(header)
            wechat.append(header)
        for record, image in figures:
            if record.selection.after_paragraph == index:
                web.append(_figure(record, image, wechat=False))
                wechat.append(_figure(record, image, wechat=True))
    links = "".join(f"<li>{_source_link(name, url)}</li>" for name, url in sources)
    footer = f"<footer><p>论文与资料来源</p><ul>{links}</ul></footer>" if links else ""
    if links:
        wechat.append(
            '<section style="margin-top:32px;padding-top:18px;border-top:1px solid #e2e8ef;'
            f'font-size:13px;line-height:1.8;color:#52647a;"><p>论文与资料来源</p><ul>{links}</ul></section>'
        )
    # 外部文章链接可点击；图片、字体和脚本不能由第三方页面加载。
    csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><article>"
        f'<header><h1>{html.escape(title)}</h1></header><main class="prose">'
        + "".join(web)
        + f"</main>{footer}</article></body></html>"
    )
    fragment = f'<section style="font-family:{html.escape(FONT, quote=True)};background:#fff;">'
    return page, fragment + "".join(wechat) + "</section>"


def _paper_header(header: PaperHeader) -> str:
    # 独立section避免编辑器将题头当作研究配图，移动端自然缩放、不裁断。
    png = base64.b64decode(header.png_base64, validate=True)
    if hashlib.sha256(png).hexdigest() != header.image_sha256:
        raise ValueError("题头图片指纹不一致")
    if not 0 < header.height <= header.width * 0.51:
        raise ValueError("题头图片比例不适合正文")
    return (
        '<section data-paper-header="true" style="margin:24px auto 28px;max-width:560px;break-inside:avoid;">'
        f'<img data-paper-header="true" src="data:image/png;base64,{header.png_base64}" '
        f'width="{header.width}" height="{header.height}" '
        'alt="论文首页题头" style="display:block;width:100%;height:auto;" />'
        "</section>"
    )


def _header_position(paragraphs: list[str]) -> int:
    # 开篇连续段落作为一个阅读单元；不得把题头插在小标题与其首段之间。
    first_text = next((i for i, line in enumerate(paragraphs) if not line.startswith("## ")), None)
    if first_text is None:
        return 0
    if first_text == 0:
        boundary = next((i for i, line in enumerate(paragraphs[1:], 1) if line.startswith("## ")), None)
        if boundary is not None:
            return boundary
    return first_text + 1
