"""导入后的串行后台执行入口；持久化状态由应用端口写入，重复确认不重复调用模型。"""

from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from threading import Lock

from agent4pharma.runtime.production_execution import ProductionPreparationPending


class PaperContinuationAPI:
    """仅接收已导入论文身份；失败不自动重试，服务退出显式关闭执行资源。"""

    def __init__(
        self,
        status: Callable[[str, str], str],
        progress: Callable[[str, str, str, str, str], None],
        run: Callable[[str, str], None],
        confirm: Callable[[str, str], None] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.status, self.progress, self.run = status, progress, run
        self.confirm = confirm
        self.owns_executor = executor is None
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-continuation")
        self.lock = Lock()
        self.pending: set[tuple[str, str]] = set()

    def confirm_import(self, run_id: str, record_id: str) -> str:
        """人工确认入口；先留存推荐，再排队。启动恢复不得冒充人工确认。"""
        if self.confirm is None:
            raise ValueError("未装配人工推荐入口")
        self.confirm(run_id, record_id)
        return self.start(run_id, record_id)

    def start(self, run_id: str, record_id: str) -> str:
        """首次导入排队；已有结果、执行中或失败状态不因重复点击而重跑。"""
        key = (run_id, record_id)
        with self.lock:
            if key in self.pending or self.status(*key) not in {"idle", "queued"}:
                return run_id + ":" + record_id
            self.progress(*key, "queued", "", "")
            self.pending.add(key)
            self.executor.submit(self._run, *key)
        return run_id + ":" + record_id

    def _run(self, run_id: str, record_id: str) -> None:
        try:
            self.run(run_id, record_id)
        except Exception as error:
            message = (
                error.public_message
                if isinstance(error, ProductionPreparationPending)
                else "自动处理失败：" + type(error).__name__
            )
            self.progress(run_id, record_id, "failed", "", message)
        finally:
            with self.lock:
                self.pending.discard((run_id, record_id))

    def close(self) -> None:
        """停止接受任务；已启动模型由其既定硬超时约束，未执行队列不暗中恢复。"""
        if self.owns_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
