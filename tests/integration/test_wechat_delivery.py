import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_production import approve_current, commission, package, review, submit_content
from test_production import production as production_fixture

from agent4pharma.modules.publishing.article_layout import LayoutRequest
from agent4pharma.modules.publishing.wechat_delivery import (
    WechatDeliveryError,
    WechatDeliveryUnknown,
    WechatDraftRequest,
    WechatPreparedDraft,
)
from agent4pharma.workflows.wechat_delivery import WechatDeliveryService


class Gateway:
    def __init__(self, app):
        self.app = app
        self.calls = 0
        self.error = None
        self.after_prepare = lambda: None

    def prepare(self, content, request):
        # 能获取另一个写事务证明网络阶段不占用 SQLite 事务。
        with self.app.unit():
            pass
        self.after_prepare()
        return WechatPreparedDraft(content.title, content.html, request.thumb_media_id)

    def create(self, prepared):
        with self.app.unit():
            pass
        self.calls += 1
        if self.error:
            raise self.error
        return "remote-draft"


production = production_fixture


@pytest.fixture
def delivery(production):
    app, root = production
    commission(app)
    submit_content(app)
    review(app)
    result = app.create_layout(LayoutRequest(package_id=package(app)))
    approve_current(app, result.id)
    gateway = Gateway(app)
    service = WechatDeliveryService(app.unit, app.read_unit, gateway, "account-a", "测试公众号")
    state = service.state(result.id)
    request = WechatDraftRequest(
        request_id=uuid4(),
        package_id=result.id,
        expected_fingerprint=state.fingerprint,
        account_id="account-a",
        thumb_media_id="existing-cover",
    )
    return service, gateway, request, app, root


def test_delivery_deduplicates_concurrent_requests_and_records_submission(delivery):
    service, gateway, request, app, _ = delivery
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(service.queue, [request, request.model_copy(update={"request_id": uuid4()})]))
    assert records[0].id == records[1].id
    service.execute(records[0].id)
    service.execute(records[0].id)
    assert gateway.calls == 1
    assert service.state(request.package_id).deliveries[0].status == "submitted"
    assert service.queue(request.model_copy(update={"request_id": uuid4()})).status == "submitted"
    with app.read_unit() as work:
        assert work.releases.receipts()[-1].status == "submitted"
        assert work.releases.receipts()[-1].remote_id == "remote-draft"


def test_unknown_result_blocks_new_request_and_restart_never_resends(delivery):
    service, gateway, request, _, _ = delivery
    gateway.error = WechatDeliveryUnknown("发送结果未知")
    record = service.queue(request)
    service.execute(record.id)
    assert service.state(request.package_id).deliveries[0].status == "unknown_delivery"
    assert service.queue(request.model_copy(update={"request_id": uuid4()})).id == record.id
    service.recover_interrupted()
    assert gateway.calls == 1


def test_explicit_failure_allows_new_attempt_but_same_uuid_stays_failed(delivery):
    service, gateway, request, _, _ = delivery
    gateway.error = WechatDeliveryError("微信拒绝请求")
    record = service.queue(request)
    service.execute(record.id)
    assert service.queue(request).status == "failed"
    retried = service.queue(request.model_copy(update={"request_id": uuid4()}))
    assert retried.id != record.id
    gateway.error = None
    service.execute(retried.id)
    assert [r.status for r in service.state(request.package_id).deliveries] == ["failed", "submitted"]


@pytest.mark.parametrize(
    "status,expected", [("queued", "failed"), ("uploading", "failed"), ("submitting", "unknown_delivery")]
)
def test_interruption_recovery_preserves_sending_boundary(delivery, status, expected):
    service, gateway, request, app, _ = delivery
    record = service.queue(request)
    with app.unit() as work:
        work.releases.save_wechat_delivery(record.model_copy(update={"status": status}))
    service.recover_interrupted()
    assert service.state(request.package_id).deliveries[0].status == expected
    assert gateway.calls == 0


def test_version_approval_account_and_uuid_are_enforced(delivery):
    service, _, request, app, root = delivery
    with pytest.raises(ValueError, match="账号"):
        service.queue(request.model_copy(update={"account_id": "other"}))
    with pytest.raises(ValueError, match="版本"):
        service.queue(request.model_copy(update={"expected_fingerprint": "0" * 64}))
    with pytest.raises(ValueError, match="封面"):
        service.queue(request.model_copy(update={"cover_sha256": "1" * 64, "thumb_media_id": ""}))
    service.queue(request)
    with pytest.raises(ValueError, match="请求 ID"):
        service.queue(request.model_copy(update={"thumb_media_id": "other"}))
    with sqlite3.connect(root / "discovery.sqlite3") as db:
        db.execute("DELETE FROM human_package_approvals")
    assert not service.state(request.package_id).available
    service.execute(str(request.request_id))
    assert service.state(request.package_id).deliveries[0].status == "failed"


def test_rechecks_approval_after_material_upload(delivery):
    service, gateway, request, _, root = delivery

    def revoke():
        with sqlite3.connect(root / "discovery.sqlite3") as db:
            db.execute("DELETE FROM human_package_approvals")

    gateway.after_prepare = revoke
    service.execute(service.queue(request).id)
    assert gateway.calls == 0
    assert service.state(request.package_id).deliveries[0].status == "failed"


def test_reading_unconfigured_service_has_no_network(delivery):
    _, gateway, request, app, _ = delivery
    service = WechatDeliveryService(app.unit, app.read_unit, None)
    state = service.state(request.package_id)
    assert not state.available and not state.configured
    assert "PHARMA_WECHAT_APP_SECRET" in state.reason
    assert gateway.calls == 0


def test_wechat_http_adapter_schedules_and_reuses_persisted_request(delivery):
    from agent4pharma.entrypoints.web.production_api import ProductionAPI

    service, gateway, request, app, _ = delivery
    api = ProductionAPI(app, lambda _: pytest.fail("模型不应执行"), lambda: False, wechat=service)
    try:
        state = api.post("/api/production/wechat-state", {"package_id": request.package_id})
        assert state["available"]
        api.post("/api/production/wechat-draft", request.model_dump(mode="json"))
    finally:
        api.close()
    assert gateway.calls == 1
    assert service.state(request.package_id).deliveries[0].status == "submitted"


def test_unknown_requires_explicit_account_bound_absence_confirmation(delivery):
    from agent4pharma.modules.publishing.wechat_delivery import WechatConfirmAbsent

    service, gateway, request, app, _ = delivery
    gateway.error = WechatDeliveryUnknown("超时")
    record = service.queue(request)
    service.execute(record.id)
    confirmation = WechatConfirmAbsent(
        delivery_id=record.id,
        account_id=request.account_id,
        confirmed_absent=True,
        reason="已登录目标公众号核对今日草稿列表",
    )
    with pytest.raises(ValueError, match="公众号"):
        service.confirm_absent(confirmation.model_copy(update={"account_id": "other"}))
    result = service.confirm_absent(confirmation)
    assert result.status == "failed" and result.error == "超时" and result.resolution
    with app.read_unit() as work:
        assert work.releases.receipts()[-1].status == "unknown_delivery"
    assert gateway.calls == 1
    assert service.queue(request.model_copy(update={"request_id": uuid4()})).id != record.id


def test_v13_migration_backup_and_rollback_preserve_old_records(tmp_path, monkeypatch):
    from agent4pharma.composition import open_application_storage
    from agent4pharma.infrastructure.persistence.sqlite import migrations

    open_application_storage(tmp_path).close()
    path = tmp_path / "discovery.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE wechat_deliveries")
        db.execute("DELETE FROM schema_version WHERE version=13")
        db.execute(
            "INSERT INTO release_receipts(id,package_id,payload) VALUES (?,?,?)",
            ("historical", "p", '{"unchanged":true}'),
        )
        db.commit()
        original = migrations.MIGRATION_13
        monkeypatch.setattr(migrations, "MIGRATION_13", original.replace("COMMIT;", "INVALID SQL; COMMIT;"))
        with pytest.raises(sqlite3.OperationalError):
            migrations.migrate(db)
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 12
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_deliveries'").fetchone()
        monkeypatch.setattr(migrations, "MIGRATION_13", original)
        migrations.migrate(db)
        migrations.migrate(db)
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 13
        assert (
            db.execute("SELECT payload FROM release_receipts WHERE id='historical'").fetchone()[0]
            == '{"unchanged":true}'
        )
    backups = sorted((tmp_path / "backups").glob("*pre-v13*"))
    with sqlite3.connect(backups[-1]) as backup:
        assert backup.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 12
        restored = sqlite3.connect(tmp_path / "restored.sqlite3")
        try:
            backup.backup(restored)
            assert (
                restored.execute("SELECT payload FROM release_receipts WHERE id='historical'").fetchone()[0]
                == '{"unchanged":true}'
            )
        finally:
            restored.close()
