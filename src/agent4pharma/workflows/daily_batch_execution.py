"""串行执行显式请求的日常批次；查询及重启不派生模型调用。"""

from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import date
from threading import Lock
from typing import Literal
from uuid import uuid4

from agent4pharma.modules.discovery.daily_batch_runs import (
    DailyBatchResult,
    DailyBatchRun,
    DailyBatchRuns,
    DailyBatchSnapshot,
    RetryPaperSelection,
    StartDailyBatch,
)
from agent4pharma.modules.discovery.news_selection import (
    NewsComparisonRequestError,
    NewsComparisonValidationError,
)
from agent4pharma.shared.clock import utcnow

BatchProgress = Callable[[Literal["collecting", "selecting"], str], None]
BatchOperation = Callable[[date, BatchProgress], DailyBatchResult]
ACTIVE = {"queued", "collecting", "selecting"}


class DailyBatchExecution:
    """持有一个执行器，短锁内只保存状态；资源由close显式释放。"""

    def __init__(
        self,
        runs: DailyBatchRuns,
        today: Callable[[], date],
        operation: BatchOperation,
        executor: Executor | None = None,
        *,
        retry_operation: Callable[[str, BatchProgress], DailyBatchResult] | None = None,
    ) -> None:
        self.runs, self.today, self.operation = runs, today, operation
        self.retry_operation = retry_operation
        self.lock = Lock()
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-batch")
        self.owns_executor = executor is None

    def snapshot(self) -> DailyBatchSnapshot:
        """读取今天最新尝试，不恢复队列或初始化外部服务。"""
        day = self.today()
        latest = next((run for run in self.runs.all() if run.day == day), None)
        return DailyBatchSnapshot(day=day, job=latest)

    def start(self, request: StartDailyBatch) -> DailyBatchRun:
        """同一请求返回原尝试，同日活跃尝试复用；跨日或其他批次占用明确拒绝。"""
        return self._submit(request)

    def retry_selection(self, request: RetryPaperSelection) -> DailyBatchRun:
        """串行重试准确冻结运行；不执行采集，同一请求幂等，其他活跃工作明确拒绝。"""
        if self.retry_operation is None:
            raise ValueError("当前后台不支持精选重试")
        return self._submit(
            StartDailyBatch(day=self.today(), request_id=request.request_id), request.selection_run_id
        )

    def _submit(self, request: StartDailyBatch, selection_run_id: str = "") -> DailyBatchRun:
        with self.lock:
            runs = self.runs.all()
            previous = next((r for r in runs if request.request_id in r.request_ids), None)
            if previous:
                if previous.selection_run_id != selection_run_id:
                    raise ValueError("幂等请求身份已用于另一个执行范围")
                if previous.day != request.day:
                    raise ValueError("幂等请求身份已用于另一个业务日")
                return previous
            if request.day != self.today():
                raise ValueError("业务日已变化，请刷新后运行今日批次")
            active = next((r for r in runs if r.status in ACTIVE), None)
            if active:
                if active.selection_run_id != selection_run_id:
                    raise ValueError("另一个批次操作仍在运行，请等待完成")
                if active.day != request.day:
                    raise ValueError("另一个业务日批次仍在运行，请等待完成")
                active = active.model_copy(update={"request_ids": (*active.request_ids, request.request_id)})
                self.runs.save(active)
                return active
            now = utcnow()
            run = DailyBatchRun(
                id="batch-run-" + uuid4().hex,
                request_ids=(request.request_id,),
                day=request.day,
                selection_run_id=selection_run_id,
                status="queued",
                created_at=now,
                updated_at=now,
            )
            self.runs.save(run)
            try:
                self.executor.submit(self._execute, run)
            except RuntimeError:
                self.runs.save(run.model_copy(update={"status": "failed", "error": "执行器已关闭，请重试"}))
                raise ValueError("执行器已关闭，请重试") from None
            return run

    def _execute(self, run: DailyBatchRun) -> None:
        def progress(status: Literal["collecting", "selecting"], batch_id: str) -> None:
            nonlocal run
            run = run.model_copy(update={"status": status, "batch_id": batch_id, "updated_at": utcnow()})
            with self.lock:
                self._save_progress(run)

        try:
            if run.selection_run_id:
                assert self.retry_operation is not None
                result = self.retry_operation(run.selection_run_id, progress)
            else:
                result = self.operation(run.day, progress)
            run = run.model_copy(
                update={
                    "status": "partial" if result.partial else "succeeded",
                    "batch_id": result.batch_id,
                    "result": result,
                    "updated_at": utcnow(),
                }
            )
        except Exception as error:
            # 任务边界只公开安全类型，来源与模型的具体错误沿原批次记录读取。
            run = run.model_copy(
                update={
                    "status": "failed",
                    "updated_at": utcnow(),
                    "error": str(error)
                    if isinstance(error, (NewsComparisonValidationError, NewsComparisonRequestError))
                    else "执行未完成（" + type(error).__name__ + "），请查看本轮诊断",
                }
            )
        with self.lock:
            self._save_progress(run)

    def _save_progress(self, run: DailyBatchRun) -> None:
        # 调用方持短锁；后台推进不能覆盖此期间其他点击登记的幂等身份。
        current = next(item for item in self.runs.all() if item.id == run.id)
        identities = tuple(dict.fromkeys((*current.request_ids, *run.request_ids)))
        self.runs.save(run.model_copy(update={"request_ids": identities}))

    def recover_interrupted(self) -> None:
        """仅由持有工作台进程锁的启动边界调用，中断尝试失败，不自动重跑。"""
        with self.lock:
            for run in self.runs.all():
                if run.status in ACTIVE:
                    self.runs.save(
                        run.model_copy(
                            update={
                                "status": "failed",
                                "updated_at": utcnow(),
                                "error": "后台已重启，上次跑批中断；已保存数据保留，请显式重试",
                            }
                        )
                    )

    def close(self) -> None:
        """关闭自有执行器；已运行网络请求由原流程超时限制。"""
        if self.owns_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
