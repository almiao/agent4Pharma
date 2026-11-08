"""每日文献发现流程：协调来源采集、本地 Decider 预选和覆盖报告；精筛由独立流程续跑。"""

from datetime import date

from agent4pharma.modules.collection.acquisition_interfaces import SourceConnector
from agent4pharma.modules.collection.source_collection import CollectionService
from agent4pharma.modules.discovery.local_triage import LocalTriage
from agent4pharma.runtime.execution_interfaces import Closeable
from agent4pharma.runtime.locking import worker_lock
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.discovery_dependencies import DailyDiscoveryStorage


class DailyLiteratureDiscovery:
    """编排一次业务日的文献发现；来源失败分开记录，部分覆盖不得伪报完整。"""

    def __init__(
        self,
        config: ApplicationSettings,
        store: DailyDiscoveryStorage,
        providers: dict[str, SourceConnector],
        *,
        transport: Closeable | None = None,
        triage: LocalTriage | None = None,
    ) -> None:
        self.config, self.store = config, store
        self.providers, self.transport = providers, transport
        self.collector = CollectionService(config, store, providers)
        self.triage = triage

    def close(self) -> None:
        """释放本对象拥有的资源；业务状态和已提交数据不随关闭改变。"""
        if self.transport:
            self.transport.close()

    def run(self, day: date) -> dict:
        """处理一个业务日并返回真实覆盖报告，不调用模型。

        Args:
            day: 按配置业务时区解释的日期，来源查询窗口由配置计算。

        Returns:
            含 batch_id、覆盖状态、候选与来源失败信息的报告。重复调用续跑原任务。

        Raises:
            RuntimeError: 数据目录已被另一个采集进程锁定。
        """
        with worker_lock(self.store.root):
            config, store = self.config, self.store
            store.seed_sources(config)
            run_id, batch_id = store.create_run(day.isoformat(), config)
            store.set_job(run_id, "running", attempt=True)
            start, end = config.window(day)
            for source, settings in config.sources.items():
                self.collector.collect_source(run_id, batch_id, source, settings, start, end)
            store.assess(batch_id, config)
            if self.triage:
                self.triage.run(batch_id)
            return store.write_report(batch_id)
