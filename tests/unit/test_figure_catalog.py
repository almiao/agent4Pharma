from agent4pharma.infrastructure.llm.mineru_figure_catalog import catalog_from_markdown


def test_catalog_keeps_real_locators_and_cross_page_caption():
    text = """<!-- page 1 of 3 -->
Fig. 6 | Rescue of B cells after perturbation.
<!-- page 2 of 3 -->
g
![Chart block](doc:abcdef0/tier:standard/page:2/block:20)
Stat3 mutant groups
![Image block](doc:abcdef0/tier:standard/page:2/block:22)
<!-- page 3 of 3 -->
Discussion only.
"""
    catalog = catalog_from_markdown(text, "a" * 64)
    assert len(catalog.pages) == 1
    page = catalog.pages[0]
    assert page.overview_id == "p2"
    assert [c.figure_id for c in page.candidates] == ["p2b20", "p2b22"]
    assert "Fig. 6" in page.caption_context
    assert "Stat3" in page.candidates[0].nearby_text


def test_catalog_does_not_invent_images_from_plain_text():
    catalog = catalog_from_markdown("<!-- page 1 of 1 --> See Fig. 2 for results.", "b" * 64)
    assert catalog.pages == []


def test_figure_image_cache_does_not_reinvoke_mineru(tmp_path, monkeypatch):
    import base64
    from pathlib import Path

    from agent4pharma.infrastructure.llm import mineru_figure_catalog as adapter
    from agent4pharma.modules.materials.document_reading import VerifiedDocument
    from agent4pharma.modules.publishing.figure_catalog import FigureCandidate, FigureCatalog, FigurePage

    sha = "c" * 64
    folder = tmp_path / sha
    folder.mkdir()
    catalog = FigureCatalog(
        document_sha256=sha,
        short_id="ccccccc",
        pages=[
            FigurePage(
                page=1,
                overview_id="p1",
                caption_context="Fig.1",
                candidates=[FigureCandidate(figure_id="p1b3", page=1, block=3, nearby_text="result")],
            )
        ],
    )
    (folder / "catalog-v2.json").write_text(catalog.model_dump_json(), "utf-8")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH2cAAAAASUVORK5CYII="
    )
    (folder / "p1b3.png").write_bytes(png)

    def reject(*args):
        raise AssertionError("cache hit should not invoke CLI or resolve identity")

    monkeypatch.setattr(adapter, "_run", reject)
    monkeypatch.setattr(adapter, "_identity", reject)
    image = adapter.inspect_cached_figure(None, VerifiedDocument(Path("bound.pdf"), sha, 1), "p1b3", tmp_path)
    assert image.png == png and image.width == 1
    import pytest

    with pytest.raises(ValueError, match="图片ID"):
        adapter.inspect_cached_figure(
            None, VerifiedDocument(Path("bound.pdf"), sha, 1), "../../secret", tmp_path
        )


def test_contact_sheet_is_navigation_only_and_labels_all_blocks():
    import base64

    import pytest

    from agent4pharma.infrastructure.llm.figure_contact_sheet import compose_figure_contact_sheet
    from agent4pharma.modules.publishing.article_layout import FigureImage

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH2cAAAAASUVORK5CYII="
    )
    original = FigureImage(png, 1, 1, "doc:abc/page:1/block:3")
    sheet = compose_figure_contact_sheet([("p1b3", original), ("p1b7", original)], "figure-index:abc/page:1")
    assert (sheet.width, sheet.height) == (800, 340)
    assert sheet.png.startswith(b"\x89PNG") and original.png == png
    with pytest.raises(ValueError, match="过多"):
        compose_figure_contact_sheet([("p1b3", original)] * 25, "figure-index:abc/page:1")
