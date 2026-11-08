"""项目收件、Chrome 和系统下载目录的有界 PDF 匹配；客户端不能提交路径。"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from agent4pharma.infrastructure.persistence.manual_pdf_directories import discover_download_locations
from agent4pharma.infrastructure.web.paper_pdf_validation import matches_main_pdf
from agent4pharma.modules.materials.manual_pdf_imports import InboxPdf
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest


class ManualPdfInbox:
    """每目录最多检查最近50个完整 PDF，拒绝目录外链接和超过100MB的文件。"""

    def __init__(self, root: Path, directories: Sequence[Path] | None = None) -> None:
        self.directory = (root / "paper-downloads/inbox").resolve()
        discovery = discover_download_locations() if directories is None else None
        extra = discovery.directories if discovery is not None else directories or ()
        self.directories = tuple(dict.fromkeys([self.directory, *(path.resolve() for path in extra)]))
        self._discovery_warnings = list(discovery.warnings) if discovery else []
        self.warnings = self._discovery_warnings.copy()

    def _files(self) -> list[Path]:
        files = []
        self.warnings = self._discovery_warnings.copy()
        for directory in self.directories:
            entries = []
            try:
                for path in directory.iterdir():
                    if path.suffix.casefold() != ".pdf" or path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                        if (
                            path.is_file()
                            and path.resolve().parent == directory
                            and 4 <= stat.st_size <= 100_000_000
                        ):
                            entries.append((stat.st_mtime_ns, path))
                    except FileNotFoundError:
                        continue
                    except OSError:
                        self.warnings.append(f"无法读取部分文件：{directory}")
            except FileNotFoundError:
                continue
            except OSError:
                self.warnings.append(f"无法完整读取下载目录：{directory}")
            files.extend(path for _, path in sorted(entries, key=lambda item: item[0], reverse=True)[:50])
        return files

    @staticmethod
    def _identity(path: Path) -> str:
        stat = path.stat()
        return hashlib.sha256(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()

    def candidates(self, request: PaperDownloadRequest) -> list[InboxPdf]:
        """按第一页实际身份匹配；不同目录的同名文件保留独立令牌和目录说明。"""
        result = []
        for path in self._files():
            try:
                size = path.stat().st_size
                if not 4 <= size <= 100_000_000:
                    continue
                before = self._identity(path)
                matched = matches_main_pdf(path, request.identifier, request.title)
                if before != self._identity(path):
                    continue
                result.append(
                    InboxPdf(
                        file_id=before,
                        filename=path.name,
                        directory=str(path.parent),
                        size_bytes=size,
                        matches=matched,
                    )
                )
            except FileNotFoundError:
                continue
            except OSError:
                self.warnings.append(f"无法读取部分 PDF：{path.parent}")
        return result

    def resolve(self, identity: str) -> Path:
        """只解析已发现目录内仍未变化的文件令牌，变更后要求重新匹配。"""
        for path in self._files():
            try:
                if self._identity(path) == identity:
                    return path
            except FileNotFoundError:
                continue
        raise ValueError("下载文件已变化或不在已检查目录，请重新匹配")
