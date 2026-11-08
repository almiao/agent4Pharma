"""人工下载确认的公开契约；只接收已登记论文和目录内文件身份，不接受任意路径。"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult


class ManualPdfConfirmation(BaseModel):
    """无文件身份时自动匹配，多个匹配时由人工选择后再确认。"""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    record_id: str
    file_id: str = Field(default="", max_length=64, pattern=r"^[a-f0-9]*$")
    recommend_and_start: bool = False


class InboxPdf(BaseModel):
    """可公开的文件候选；文件身份绑定绝对位置、大小和修改时间。"""

    file_id: str
    filename: str
    directory: str = ""
    size_bytes: int
    matches: bool


class ManualPdfAssociation(BaseModel):
    """关联回执；只有文件通过主文校验才标 imported。"""

    status: Literal["imported", "choose_file", "not_found", "validation_failed"]
    directory: str
    directories: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    filename: str = ""
    result_id: str = ""
    candidates: list[InboxPdf] = Field(default_factory=list)


class ManualPdfImports:
    """人工确认后匹配一次并复制，歧义时选择文件；不启动下载、模型或修改排序。"""

    def __init__(
        self,
        directory: str,
        binding: Callable[[str, str], PaperDownloadRequest],
        existing: Callable[[PaperDownloadRequest], PaperDownloadResult | None],
        candidates: Callable[[PaperDownloadRequest], list[InboxPdf]],
        import_file: Callable[[str, str, str], PaperDownloadResult],
        directories: list[str] | None = None,
        warnings: Callable[[], list[str]] | None = None,
    ) -> None:
        self.directory, self.binding, self.existing = directory, binding, existing
        self.candidates, self.import_file = candidates, import_file
        self.directories = directories if directories is not None else [directory]
        self.warnings = warnings or (lambda: [])

    def confirm(self, confirmation: ManualPdfConfirmation) -> ManualPdfAssociation:
        """重复确认复用有效文件；只允许精确匹配的主文，客户端不能提交路径或绕过校验。"""
        request = self.binding(confirmation.run_id, confirmation.record_id)
        existing = self.existing(request)
        if existing is not None:
            return ManualPdfAssociation(
                status="imported",
                directory=self.directory,
                directories=self.directories,
                warnings=self.warnings(),
                result_id=existing.id,
                filename=str(existing.runtime_profile.get("original_filename", "")),
            )
        candidates = self.candidates(request)
        matches = [file for file in candidates if file.matches]
        selected = (
            next((file for file in matches if file.file_id == confirmation.file_id), None)
            if confirmation.file_id
            else matches[0]
            if len(matches) == 1
            else None
        )
        if selected is None:
            return ManualPdfAssociation(
                status="choose_file" if matches else "not_found",
                directory=self.directory,
                directories=self.directories,
                warnings=self.warnings(),
                candidates=candidates,
            )
        result = self.import_file(confirmation.run_id, confirmation.record_id, selected.file_id)
        return ManualPdfAssociation(
            status="imported" if result.status == "downloaded" else "validation_failed",
            directory=self.directory,
            directories=self.directories,
            warnings=self.warnings(),
            filename=selected.filename,
            result_id=result.id,
        )
