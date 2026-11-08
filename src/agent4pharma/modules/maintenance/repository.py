"""样本、任务、证据与本地查重的持久化契约。"""

from dataclasses import dataclass
from typing import Protocol

from agent4pharma.modules.maintenance.models import (
    ArticleCase,
    ArticleCaseInput,
    InvestigationJob,
    InvestigationReport,
    InvestigationRequest,
    LocalMaterialMatch,
    PageEvidence,
)


@dataclass(frozen=True)
class FetchedDocument:
    """受限 HTTP 访问的原始结果，原始字节仅交证据仓储留档。"""

    url: str
    body: bytes
    content_type: str
    http_status: int


class PageFetcher(Protocol):
    """访问公开网页的边界，不支持本机、私网、文件或任意 Shell。"""

    def fetch(self, url: str) -> FetchedDocument:
        """有限次重定向后取得有大小限制的公开响应。"""
        ...


class MaintenanceRepository(Protocol):
    """持久化人工输入与版本化调查；原始结果不能被人工标签覆盖。"""

    def list_cases(self) -> list[ArticleCase]:
        """返回近期样本及人工判断。"""
        ...

    def save_case(self, change: ArticleCaseInput, case_id: str | None = None) -> ArticleCase:
        """创建或按 revision 更新样本，保留审计记录。"""
        ...

    def get_case(self, case_id: str) -> ArticleCase:
        """取得样本，不存在时抛出 KeyError。"""
        ...

    def create_run(self, request: InvestigationRequest) -> InvestigationJob:
        """保存不可变请求快照，相同 request_id 幂等返回原任务。"""
        ...

    def get_job(self, job_id: str) -> InvestigationJob:
        """读取一个任务的请求、状态和结果。"""
        ...

    def list_jobs(self) -> list[InvestigationJob]:
        """返回近期运行历史，不隐藏失败任务。"""
        ...

    def update_job(
        self, job_id: str, status: str, report: InvestigationReport | None = None, error: str | None = None
    ) -> None:
        """保存有证据的运行状态，error 只接受脱敏文字。"""
        ...


class EvidenceCatalog(Protocol):
    """任务绑定的原始访问证据，不向模型开放任意文件。"""

    def list_evidence(self, job_id: str) -> list[PageEvidence]:
        """返回绑定任务的所有访问结果，包括失败。"""
        ...

    def save_evidence(self, page: PageEvidence, body: bytes = b"") -> PageEvidence:
        """内容寻址留档并保存结构化证据，不允许越过任务页面预算。"""
        ...


class MaterialLookup(Protocol):
    """查找现有资料，不将手工提交自动插入生产资料。"""

    def find_material(self, reference: str) -> list[LocalMaterialMatch]:
        """按标准身份或精确 URL 查找，区分主库、smoke 与 demo。"""
        ...
