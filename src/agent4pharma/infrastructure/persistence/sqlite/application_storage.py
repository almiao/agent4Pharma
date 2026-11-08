"""Connection owner and compatibility facade; business code uses narrow ports."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.collection_page_repository import CollectionRepository
from agent4pharma.infrastructure.persistence.sqlite.discovery_batch_repository import DiscoveryRepository
from agent4pharma.infrastructure.persistence.sqlite.job_repository import JobRepository
from agent4pharma.infrastructure.persistence.sqlite.material_repository import MaterialRepository
from agent4pharma.infrastructure.persistence.sqlite.migrations import migrate
from agent4pharma.infrastructure.persistence.sqlite.source_access_repository import AccessRepository
from agent4pharma.modules.materials.collection_records import CollectionPage
from agent4pharma.modules.materials.material_identity import identity
from agent4pharma.runtime.settings import ApplicationSettings


class SqliteApplicationStorage:
    """当前单机发现应用的持久化适配器及连接所有者。

    组合现有任务、资料、采集与目录仓储，保留发现试点的持久化 API。
    新业务模块应注入对应仓储，不继续扩充这个聚合对象；db 不得交给业务层。
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "discovery.sqlite3", timeout=30)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            migrate(self.db)
            self.access = AccessRepository(self.root, self.db)
            self.materials = MaterialRepository(self.root, self.db)
            self.jobs = JobRepository(self.root, self.db)
            self.discovery = DiscoveryRepository(self.root, self.db)
            self.collection = CollectionRepository(self.root, self.db)
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        """释放本对象拥有的资源；业务状态和已提交数据不随关闭改变。"""
        self.db.close()

    identity = staticmethod(identity)

    def seed_sources(self, config: ApplicationSettings):
        """幂等更新采集来源及共享访问额度配置，保留已有关联元数据。"""
        return self.access.seed_sources(config)

    def reserve_request(self, group: str, interval: float) -> float:
        """原子预约共享访问额度，返回需要等待的秒数；实际等待必须发生在事务之外。"""
        return self.access.reserve_request(group, interval)

    def put_artifact(
        self,
        body: bytes,
        kind: str,
        provenance: dict,
        material_id: str | None = None,
        record_id: str | None = None,
    ) -> str:
        """按内容哈希保存文件并登记出处；参与调用方事务，不自行提交数据库。"""
        return self.materials.put_artifact(body, kind, provenance, material_id, record_id)

    def material(self, material_id: str) -> dict:
        """返回资料、来源版本和产物引用；不存在时抛出 KeyError，访问范围由上层服务校验。"""
        return self.materials.material(material_id)

    def read_artifact(self, artifact_id: str, offset: int = 0, length: int = 12000) -> dict:
        """按字符偏移读取产物片段及出处；拒绝非法长度、负偏移和越界存储路径。"""
        return self.materials.read_artifact(artifact_id, offset, length)

    def source_job(self, run_id: str, source: str, request: dict) -> dict:
        """为一个运行和来源幂等创建采集任务；重试保留原请求窗口与已保存游标。"""
        return self.jobs.source_job(run_id, source, request)

    def job(self, job_id: str) -> dict:
        """读取逻辑任务及反序列化的请求/结果；不存在时抛出 KeyError。"""
        return self.jobs.job(job_id)

    def set_job(self, job_id: str, status: str, *, result=None, error=None, attempt=False):
        """保存任务状态、结果和脱敏错误；attempt=True 才增加尝试计数。"""
        return self.jobs.set_job(job_id, status, result=result, error=error, attempt=attempt)

    def create_run(self, day: str, config: ApplicationSettings) -> tuple[str, str]:
        """按业务日和策略指纹幂等创建运行及发现批次，返回二者 ID。"""
        return self.discovery.create_run(day, config)

    def assess(self, batch_id: str, config: ApplicationSettings):
        """对实际元数据执行确定性粗筛，保留待语义判断及同策略历史。"""
        return self.discovery.assess(batch_id, config)

    def items(self, batch_id: str, after: int = 0, limit: int = 100, selected_only: bool = False) -> dict:
        """按条目 ID 分页返回批次记录，limit 为 1..100，可限定已入选资料。"""
        return self.discovery.items(batch_id, after, limit, selected_only)

    def report(self, batch_id: str) -> dict:
        """构造真实覆盖与候选投影；部分来源失败或判断未完成时不得报告 complete。"""
        return self.discovery.report(batch_id)

    def commit_page(self, batch_id: str, job_id: str, page: CollectionPage, scope_id: str):
        """原子保存单页资料、批次成员与下一游标；失败整页回滚数据库。"""
        return self.collection.commit_page(batch_id, job_id, page, scope_id)

    def write_report(self, batch_id: str) -> dict:
        """先写出报告文件，再记录报告引用与批次状态；返回与文件一致的数据投影。"""
        from agent4pharma.modules.discovery.batch_reports import write_report_files

        report = self.report(batch_id)
        path = write_report_files(self.root, report)
        self.discovery.record_report(batch_id, report["status"], str(path.relative_to(self.root)))
        return report

    def import_catalog(self, seed_path: Path, inventory_path: Path) -> dict:
        """在同一事务中导入整理期刊、外部观察标签和出处，重复导入不新增重复记录。"""
        from agent4pharma.infrastructure.persistence.sqlite.catalog_importer import import_catalog

        return import_catalog(self, seed_path, inventory_path)

    def list_catalog(self, status: str | None = None, limit: int = 200) -> list[dict]:
        """按关注状态查询载体与最近指标；缺失指标保持 None，不转换为零。"""
        from agent4pharma.infrastructure.persistence.sqlite.venue_repository import list_catalog

        return list_catalog(self, status, limit)

    def import_demo(self, seed_path: Path) -> dict:
        """原子导入 demo 命名空间的目录和资料；所有新入口关闭，不进入生产发现批次。"""
        from agent4pharma.infrastructure.persistence.sqlite.source_directory_repository import import_demo

        return import_demo(self, seed_path)

    def inspect_demo(self) -> dict:
        """返回 demo 数据集的目录、资料和来源关系投影，不执行网络请求。"""
        from agent4pharma.infrastructure.persistence.sqlite.source_directory_repository import inspect_demo

        return inspect_demo(self)

    def demo_endpoints(self) -> list[dict]:
        """查询 demo 来源入口及运营机构关系，供报告展示真实数据库状态。"""
        from agent4pharma.infrastructure.persistence.sqlite.source_directory_repository import demo_endpoints

        return demo_endpoints(self)

    def has_batch_material(self, batch_id: str, material_id: str) -> bool:
        """判断资料是否属于绑定批次，不推定其是否已入选。"""
        return self.discovery.has_material(batch_id, material_id)

    def artifact_material(self, artifact_id: str) -> str | None:
        """返回产物关联的资料 ID；产物缺失或未关联资料时返回 None。"""
        return self.materials.artifact_material(artifact_id)

    def daily_jobs(self) -> list[dict]:
        """返回每日发现任务及其状态，供调度计算补跑日期。"""
        return self.jobs.daily_jobs()

    def ensure_collection_task(self, scope_id: str, source: str, settings: dict) -> dict:
        """幂等创建采集范围记录并返回保存状态，不覆盖已有检查点。"""
        return self.collection.ensure_task(scope_id, source, settings)

    def defer_access(self, group: str, delay: float) -> None:
        """把共享额度的下一可访问时间至少推迟 delay 秒，供后续进程重试遵守。"""
        self.access.defer_access(group, delay)

    def archive_response(self, body: bytes, provenance: dict) -> str:
        """保存原始成功响应及出处，并提交本次留档，解析失败也可追溯响应。"""
        with self.db:
            return self.materials.put_artifact(body, "raw_response", provenance)
