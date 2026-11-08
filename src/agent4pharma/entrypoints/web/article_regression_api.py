"""旧单篇回归的只读记录与观测查询。"""

import json

from agent4pharma.modules.operations.article_regression_contracts import ArticleRegressionRepository
from agent4pharma.runtime.workflow_observation import ObservationSink
from agent4pharma.workflows.production_queue import PRODUCTION_TRANSITIONS
from agent4pharma.workflows.production_workbench import ProductionWorkbench

STAGES = (
    ("acquisition", "获取原文"),
    ("archive", "归档"),
    ("coarse", "范围粗筛"),
    ("maintenance", "来源维护"),
    ("intelligence", "情报精筛"),
    ("candidate", "正式候选"),
    ("editor", "主编立项"),
    ("writer", "调查与写作"),
    ("reviewer", "编辑核验"),
    ("publisher", "发行制包"),
    ("export", "本地导出"),
)


class ArticleRegressionAPI:
    """历史回归只读查询，不创建执行器或提供续跑入口。"""

    def __init__(
        self,
        repository: ArticleRegressionRepository,
        observations: ObservationSink,
        production: ProductionWorkbench,
    ) -> None:
        """只持有历史记录读取依赖，不创建执行器。"""
        self.repository, self.observations, self.production = repository, observations, production

    def get(self, path: str) -> dict:
        """返回真实运行列表，或每阶段事件、业务引用和配置。"""
        if path == "/api/article-regressions":
            return {"items": [run.model_dump(mode="json") for run in self.repository.list()]}
        identity = path.removeprefix("/api/article-regressions/")
        run = self.repository.get(identity)
        tasks = []
        revision = 0
        if run.story_ref:
            snapshot = self.production.snapshot()
            revision = next(s.revision for s in snapshot.stories if s.id == run.story_ref)
            tasks = [t for t in snapshot.tasks if t.story_id == run.story_ref or t.id in run.subject_refs]
        subjects = [run.id, *run.subject_refs, run.assessment_ref, "investigate-" + run.id]
        events = self.observations.list(list(dict.fromkeys(filter(None, subjects))))
        for event in events:
            if event.operation == "evaluate_intelligence" and event.status == "succeeded":
                result = json.loads(event.output_json)
                if isinstance(result, dict) and str(result.get("id", "")).startswith("intel-"):
                    subjects.append(result["id"])
        events = self.observations.list(list(dict.fromkeys(filter(None, subjects))))
        stages = []
        for stage, label in STAGES:
            steps = [e for e in events if e.stage == stage]
            # 角色结束事件优先于内部工具成功，避免一次工具成功掩盖整个节点失败。
            terminal = [e for e in steps if e.subject_ref == run.id]
            status = (terminal[-1] if terminal else steps[-1]).status if steps else "not_run"
            active = [
                t
                for t in tasks
                if t.role == stage and t.expected_revision == revision and t.status in {"queued", "running"}
            ]
            if active:
                status = "started" if active[-1].status == "running" else "queued"
            stages.append({"id": stage, "label": label, "status": status, "events": len(steps)})
        transitions = [
            {"source": source, "target": target, "condition": condition}
            for source, target, condition in (
                ("acquisition", "archive", "取得可解析资料"),
                ("archive", "coarse", "保存原件与来源快照"),
                ("archive", "maintenance", "旁路来源调查，建议待人工确认"),
                ("coarse", "intelligence", "进入待判断且允许模型"),
                ("intelligence", "candidate", "正式模式推荐"),
                ("candidate", "editorial", "正式候选已提交"),
                ("publisher", "export", "准确版本渠道包有效"),
            )
        ] + [edge.model_dump() for edge in PRODUCTION_TRANSITIONS.values()]
        return {
            "run": run.model_dump(mode="json"),
            "stages": stages,
            "events": [e.model_dump(mode="json") for e in events],
            "transitions": transitions,
            "tasks": [
                t.model_dump(mode="json", exclude={"lease_token", "prompt", "input_json"}) for t in tasks
            ],
        }
