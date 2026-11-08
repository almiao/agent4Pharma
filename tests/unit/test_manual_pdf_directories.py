import json
import os

import pytest

from agent4pharma.infrastructure.persistence.manual_pdf_directories import discover_download_locations
from agent4pharma.infrastructure.persistence.manual_pdf_inbox import ManualPdfInbox
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest


def test_discovery_uses_only_download_preferences_and_deduplicates(tmp_path):
    chrome = tmp_path / "Chrome"
    for name, download in [
        ("Default", tmp_path / "BrowserDownloads"),
        ("Profile 1", tmp_path / "BrowserDownloads"),
    ]:
        profile = chrome / name
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text(
            json.dumps(
                {
                    "download": {"default_directory": str(download)},
                    "savefile": {"default_directory": str(tmp_path / "SaveAs")},
                    "other_setting": "never expose unrelated preferences",
                }
            ),
            encoding="utf-8",
        )
    broken = chrome / "Profile 2"
    broken.mkdir()
    (broken / "Preferences").write_text("{invalid", encoding="utf-8")
    locations = discover_download_locations(
        home=tmp_path, chrome_root=chrome, system_directory=tmp_path / "MovedDownloads"
    )
    assert set(locations.directories) == {
        tmp_path / "MovedDownloads",
        tmp_path / "Downloads",
        tmp_path / "BrowserDownloads",
        tmp_path / "SaveAs",
    }
    assert len(locations.directories) == 4
    assert len(locations.warnings) == 1 and "Profile 2" in locations.warnings[0]
    assert "never expose" not in str(locations)


def test_tokens_bind_the_directory_and_changed_files_require_rematching(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
        path = directory / "paper.pdf"
        path.write_bytes(b"%PDF same bytes")
        os.utime(path, ns=(1000000, 1000000))
    inbox = ManualPdfInbox(tmp_path, [first, second, first])
    assert len(inbox.directories) == 3
    first_id, second_id = [inbox._identity(directory / "paper.pdf") for directory in (first, second)]
    assert first_id != second_id
    assert inbox.resolve(second_id) == second / "paper.pdf"
    (second / "paper.pdf").write_bytes(b"%PDF changed")
    with pytest.raises(ValueError, match="重新匹配"):
        inbox.resolve(second_id)
    assert inbox.resolve(first_id) == first / "paper.pdf"


def test_recent_limit_is_per_directory_and_skips_partial_nested_and_external_links(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for index in range(55):
        (first / f"unrelated-{index}.pdf").write_bytes(b"%PDF fixture")
    (second / "target.pdf").write_bytes(b"%PDF fixture")
    (second / "partial.pdf.crdownload").write_bytes(b"%PDF fixture")
    (second / "nested").mkdir()
    (second / "nested/hidden.pdf").write_bytes(b"%PDF fixture")
    inbox = ManualPdfInbox(tmp_path, [first, second])
    files = inbox._files()
    assert len(files) == 51
    assert second / "target.pdf" in files
    assert not any(path.name in {"partial.pdf.crdownload", "hidden.pdf"} for path in files)


def test_match_does_not_accept_a_file_that_changes_during_inspection(tmp_path, monkeypatch):
    path = tmp_path / "download.pdf"
    path.write_bytes(b"%PDF fixture")
    inbox = ManualPdfInbox(tmp_path, [tmp_path])

    def changing(path, identifier, title):
        path.write_bytes(b"%PDF replaced during match")
        return True

    monkeypatch.setattr("agent4pharma.infrastructure.persistence.manual_pdf_inbox.matches_main_pdf", changing)
    request = PaperDownloadRequest(
        batch_id="batch",
        decision_id="d",
        material_ref="m",
        source_id="source",
        source_record_id="record",
        journal="",
        url="",
        title="A paper",
    )
    assert inbox.candidates(request) == []
