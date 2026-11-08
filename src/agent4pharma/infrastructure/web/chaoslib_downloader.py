"""Chaoslib 单篇下载边界：持久预算、账号互斥、PDF 身份校验及人工接入状态。"""

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from agent4pharma.infrastructure.persistence.chaoslib_access import ChaosLibAccess
from agent4pharma.infrastructure.web.chaoslib import (
    BLOCKED_PREFIXES,
    ChaosLibAuthError,
    ChaosLibConnector,
    ChaosLibError,
    load_settings,
    route_for,
)
from agent4pharma.infrastructure.web.paper_pdf_validation import validate_pdf
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest, PaperDownloadResult
from agent4pharma.runtime.locking import worker_lock
from agent4pharma.shared.clock import utcnow


@dataclass
class ChaosLibDownloader:
    """仅接受宿主绑定的资料，默认不限每日次数；失败不伪装为已读全文。"""

    root: Path
    project_root: Path = Path(".")

    def download(
        self, request: PaperDownloadRequest, existing: PaperDownloadResult | None = None
    ) -> PaperDownloadResult:
        """缓存复核不联网；新请求只尝试一次，凭证、Cookie 和站点原始错误不进入报告。"""
        result = PaperDownloadResult(
            id=uuid.uuid4().hex,
            request=request,
            status="failed",
            identifier=request.identifier,
            channel="chaoslib",
            created_at=utcnow(),
        )
        if existing:
            path = Path(existing.file).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError("待复核文件不在本项目资料目录内")
            return result.model_copy(
                update={**validate_pdf(path, request.identifier, request.title), "operation": "revalidate"}
            )
        doi = request.identifier
        if not doi.startswith("10."):
            return result.model_copy(update={"status": "unresolved", "reason": "missing_doi"})
        if route_for(doi) is None:
            reason = BLOCKED_PREFIXES.get(doi.split("/", 1)[0], "no_route")
            return result.model_copy(
                update={
                    "status": "manual_required" if "publisher_challenge" in reason else "unavailable",
                    "reason": reason,
                }
            )
        try:
            settings = load_settings(self.project_root.resolve())
            with worker_lock(settings.state_dir):
                access = ChaosLibAccess(settings.state_dir)
                reason = access.reserve()
                if reason:
                    return result.model_copy(
                        update={
                            "status": "manual_required"
                            if reason == "account_blocked_requires_manual"
                            else "cooldown",
                            "reason": reason,
                        }
                    )
                try:
                    with ChaosLibConnector(settings) as connector:
                        observed = connector.download(
                            doi, self.root / "paper-downloads" / "chaoslib", title=request.title
                        )
                except ChaosLibAuthError:
                    access.block("authentication_required")
                    return result.model_copy(
                        update={"status": "manual_required", "reason": "authentication_required"}
                    )
                except (ChaosLibError, httpx.HTTPError) as exc:
                    # 站点错误可能回显认证信息；只保留分类代码。
                    message = str(exc)
                    reason = (
                        "blocked_automation"
                        if "封禁" in message or "自动化访问" in message
                        else "rate_limited"
                        if "频繁" in message or "临时限制" in message or "rate_limited" in message
                        else "channel_error"
                    )
                    if reason != "channel_error":
                        access.block(reason)
                    return result.model_copy(
                        update={
                            "status": "manual_required"
                            if reason == "blocked_automation"
                            else "cooldown"
                            if reason == "rate_limited"
                            else "failed",
                            "reason": reason,
                        }
                    )
                if (
                    observed.reason in {"blocked_automation", "authentication_required"}
                    or "rate_limited" in observed.reason
                ):
                    access.block(observed.reason)
                result.attempts = [
                    {"kind": str(a.get("kind", "")), "status": a.get("status")} for a in observed.attempts
                ]
                if observed.status == "downloaded":
                    path = Path(observed.file).resolve()
                    if not path.is_relative_to(self.root.resolve()):
                        raise ValueError("下载结果超出资料目录")
                    return result.model_copy(update=validate_pdf(path, doi, request.title))
                status = (
                    "manual_required"
                    if observed.reason
                    in {"publisher_challenge", "blocked_automation", "authentication_required"}
                    else "cooldown"
                    if "rate_limited" in observed.reason
                    else "failed"
                )
                return result.model_copy(
                    update={
                        "status": status,
                        "reason": observed.reason
                        if observed.reason
                        in {
                            "publisher_challenge",
                            "blocked_automation",
                            "authentication_required",
                            "rate_limited:site_throttle",
                        }
                        else observed.reason
                        if re.fullmatch(
                            r"(?:article|pdf)_http_(?:\d{3}|error)|timeout|channel_error|not_pdf|unreadable_pdf",
                            observed.reason,
                        )
                        else "entry_unavailable"
                        if observed.reason.startswith("error:")
                        else "download_not_completed",
                    }
                )
        except ChaosLibAuthError:
            return result.model_copy(
                update={"status": "manual_required", "reason": "credentials_or_browser_required"}
            )
        except RuntimeError:
            return result.model_copy(update={"status": "cooldown", "reason": "account_in_use"})
