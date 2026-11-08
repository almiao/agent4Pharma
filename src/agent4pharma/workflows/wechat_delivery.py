"""公众号草稿交付用例：短事务占位，事务外上传，发送前复核批准版本。"""

from collections.abc import Callable
from contextlib import AbstractContextManager

from agent4pharma.modules.publishing.human_approval import package_fingerprint
from agent4pharma.modules.publishing.release_contracts import ChannelPackage, ReleaseReceipt
from agent4pharma.modules.publishing.wechat_content import prepare_wechat_content
from agent4pharma.modules.publishing.wechat_delivery import (
    WechatConfirmAbsent,
    WechatDeliveryError,
    WechatDeliveryRecord,
    WechatDeliveryState,
    WechatDeliveryUnknown,
    WechatDraftGateway,
    WechatDraftRequest,
    WechatImageOption,
)
from agent4pharma.shared.clock import utcnow
from agent4pharma.workflows.human_package_approval import require_reviewable_package
from agent4pharma.workflows.production_dependencies import ProductionUnit

BLOCKING = {"queued", "uploading", "submitting", "submitted", "unknown_delivery"}


def _package(work: ProductionUnit, identity: str) -> ChannelPackage:
    package = next((p for p in work.releases.packages() if p.id == identity), None)
    if package is None:
        raise KeyError("渠道包不存在")
    return package


def _approved(work: ProductionUnit, package: ChannelPackage, fingerprint: str) -> None:
    require_reviewable_package(work, package)
    if fingerprint != package_fingerprint(package):
        raise ValueError("图文版本已变化，请重新打开交付窗口")
    if not any(
        a.package_id == package.id and a.fingerprint == fingerprint for a in work.releases.approvals()
    ):
        raise ValueError("请先在人工审稿中批准此准确发布包")


class WechatDeliveryService:
    """不拥有线程；入口串行执行，事务内防重同时保护多线程重复提交。"""

    def __init__(
        self,
        unit: Callable[[], AbstractContextManager[ProductionUnit]],
        read_unit: Callable[[], AbstractContextManager[ProductionUnit]],
        gateway: WechatDraftGateway | None,
        account_id: str = "",
        account_name: str = "公众号",
    ) -> None:
        self.unit, self.read_unit, self.gateway = unit, read_unit, gateway
        self.account_id, self.account_name = account_id, account_name

    def state(self, package_id: str) -> WechatDeliveryState:
        """只读本地状态和可交付性，不获取 token 或访问微信。"""
        with self.read_unit() as work:
            package = _package(work, package_id)
            fingerprint = package_fingerprint(package)
            images, reason = [], ""
            try:
                _approved(work, package, fingerprint)
                content = prepare_wechat_content(package, work.drafts.get(package.draft_id).submission.title)
                labels = {f.image_sha256: f.selection.label for f in package.presentation.figures}
                images = [
                    WechatImageOption(sha256=i.sha256, label=labels.get(i.sha256, "论文题头"))
                    for i in content.images
                ]
            except ValueError as exc:
                reason = str(exc)
            if self.gateway is None:
                reason = (
                    reason
                    or "未配置公众号账号；请设置 PHARMA_WECHAT_APP_ID 和 "
                    "PHARMA_WECHAT_APP_SECRET 后重启运行台"
                )
            records = [r for r in work.releases.wechat_deliveries() if r.request.package_id == package_id]
            return WechatDeliveryState(
                configured=self.gateway is not None,
                account_id=self.account_id,
                account_name=self.account_name,
                fingerprint=fingerprint,
                available=not reason,
                reason=reason,
                images=images,
                deliveries=records,
            )

    def queue(self, request: WechatDraftRequest) -> WechatDeliveryRecord:
        """UUID内容绑定；同账号同准确包已有成功/在途/未知结果时返回已有记录。"""
        if self.gateway is None or request.account_id != self.account_id:
            raise ValueError("公众号未配置或目标账号已变化，请重新打开交付窗口")
        with self.unit() as work:
            records = work.releases.wechat_deliveries()
            previous = next((r for r in records if r.id == str(request.request_id)), None)
            if previous:
                if previous.request != request:
                    raise ValueError("请求 ID 已绑定其他交付内容")
                return previous
            package = _package(work, request.package_id)
            _approved(work, package, request.expected_fingerprint)
            content = prepare_wechat_content(package, work.drafts.get(package.draft_id).submission.title)
            if request.cover_sha256 and request.cover_sha256 not in {i.sha256 for i in content.images}:
                raise ValueError("封面不属于此准确图文包")
            previous = next(
                (
                    r
                    for r in records
                    if r.request.package_id == package.id
                    and r.request.account_id == self.account_id
                    and r.status in BLOCKING
                ),
                None,
            )
            if previous:
                return previous
            record = WechatDeliveryRecord(
                id=str(request.request_id),
                request=request,
                status="queued",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            work.releases.save_wechat_delivery(record)
            return record

    def execute(self, identity: str) -> None:
        """仅处理已持久化请求；异常按实际发送边界归类，未知错误不泄露原文。"""
        submitting = False
        try:
            with self.unit() as work:
                record = next(r for r in work.releases.wechat_deliveries() if r.id == identity)
                if record.status != "queued":
                    return
                package = _package(work, record.request.package_id)
                _approved(work, package, record.request.expected_fingerprint)
                content = prepare_wechat_content(package, work.drafts.get(package.draft_id).submission.title)
                work.releases.save_wechat_delivery(
                    record.model_copy(update={"status": "uploading", "updated_at": utcnow()})
                )
            if self.gateway is None or record.request.account_id != self.account_id:
                raise WechatDeliveryError("公众号配置已变化，未发送")
            prepared = self.gateway.prepare(content, record.request)
            with self.unit() as work:
                _approved(
                    work, _package(work, record.request.package_id), record.request.expected_fingerprint
                )
                work.releases.save_wechat_delivery(
                    record.model_copy(update={"status": "submitting", "updated_at": utcnow()})
                )
            submitting = True
            remote_id = self.gateway.create(prepared)
            self._finish(identity, "submitted", remote_id=remote_id)
        except WechatDeliveryUnknown as exc:
            self._finish(identity, "unknown_delivery", error=str(exc))
        except WechatDeliveryError as exc:
            self._finish(identity, "failed", error=str(exc))
        except ValueError as exc:
            self._finish(
                identity,
                "unknown_delivery" if submitting else "failed",
                error=str(exc) if not submitting else "交付结果保存异常，请核对草稿箱",
            )
        except Exception as exc:
            self._finish(
                identity,
                "unknown_delivery" if submitting else "failed",
                error="交付异常：" + type(exc).__name__,
            )

    def _finish(self, identity: str, status: str, *, error: str = "", remote_id: str = "") -> None:
        with self.unit() as work:
            record = next(r for r in work.releases.wechat_deliveries() if r.id == identity)
            updated = WechatDeliveryRecord.model_validate(
                {
                    **record.model_dump(),
                    "status": status,
                    "error": error,
                    "remote_id": remote_id,
                    "updated_at": utcnow(),
                }
            )
            work.releases.save_wechat_delivery(updated)
            if status in {"submitted", "unknown_delivery", "failed"}:
                work.releases.receipt(
                    ReleaseReceipt(
                        id="wechat-" + identity,
                        package_id=record.request.package_id,
                        status=status,
                        remote_id=remote_id,
                        created_at=utcnow(),
                    )
                )

    def recover_interrupted(self) -> None:
        """持有运行台独占锁时调用；不恢复网络动作，提交中断按未知处理。"""
        with self.read_unit() as work:
            records = work.releases.wechat_deliveries()
        for record in records:
            if record.status in {"queued", "uploading", "submitting"}:
                unknown = record.status == "submitting"
                self._finish(
                    record.id,
                    "unknown_delivery" if unknown else "failed",
                    error="服务中断；请在公众号后台核对，未自动重发"
                    if unknown
                    else "服务中断，尚未创建草稿，可重新交付",
                )

    def confirm_absent(self, request: WechatConfirmAbsent) -> WechatDeliveryRecord:
        """保留未知回执及原错误，记录人工核对理由；后续须显式创建新请求。"""
        with self.unit() as work:
            record = next(
                (r for r in work.releases.wechat_deliveries() if r.id == str(request.delivery_id)), None
            )
            if record is None:
                raise KeyError("交付记录不存在")
            if request.account_id != self.account_id or record.request.account_id != self.account_id:
                raise ValueError("目标公众号已变化，请核对原账号")
            if record.resolution:
                return record
            if record.status != "unknown_delivery":
                raise ValueError("只有结果未知的交付可以登记人工核对")
            updated = record.model_copy(
                update={
                    "status": "failed",
                    "resolution": "人工确认草稿不存在：" + request.reason,
                    "updated_at": utcnow(),
                }
            )
            work.releases.save_wechat_delivery(updated)
            return updated
