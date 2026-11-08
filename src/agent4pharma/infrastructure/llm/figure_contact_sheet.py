"""将一个已定位图页的缓存子图排成带ID的检索缩略图；不修改发布用原图。"""

from agent4pharma.modules.publishing.article_layout import FigureImage


def compose_figure_contact_sheet(images: list[tuple[str, FigureImage]], locator: str) -> FigureImage:
    """固定三列，仅供模型定位子图；原尺寸图必须另行读取后才能提交。"""
    import pymupdf

    if not 1 <= len(images) <= 24:
        raise ValueError("单页图块过多，请直接选择子图ID，不生成全篇拼图")
    columns = min(3, len(images))
    rows = (len(images) + columns - 1) // columns
    width, height = columns * 400, rows * 340
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=height)
        for index, (identity, image) in enumerate(images):
            left, top = (index % columns) * 400, (index // columns) * 340
            page.insert_text(
                (left + 12, top + 24), identity, fontsize=18, fontname="helv", color=(0.05, 0.18, 0.28)
            )
            page.insert_image(
                pymupdf.Rect(left + 12, top + 38, left + 388, top + 328),
                stream=image.png,
                keep_proportion=True,
            )
        pixmap = page.get_pixmap(alpha=False)
        return FigureImage(pixmap.tobytes("png"), pixmap.width, pixmap.height, locator)
