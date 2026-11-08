"""全来源本地预选用例：版本化复用、有限批处理与二分类路由，不产主编候选。"""

import hashlib

from agent4pharma.modules.discovery.triage_contracts import (
    LocalDecisionModel,
    ModelDecision,
    TriageDecision,
    TriageInput,
    TriagePolicy,
    TriageStore,
)
from agent4pharma.shared.clock import utcnow


class LocalTriage:
    """数据持久化和本地推理通过窄端口注入，网络不在事务内。"""

    def __init__(self, store: TriageStore, model: LocalDecisionModel, revision: str, policy: TriagePolicy):
        self.store, self.model, self.revision, self.policy = store, model, revision, policy

    def identity(self, item: TriageInput) -> str:
        """相同文章内容、模型和策略可跨批次复用，版本变化重新判别。"""
        return hashlib.sha256(
            (item.content_hash() + self.revision + self.policy.content_hash()).encode()
        ).hexdigest()

    def run(self, batch_id: str) -> None:
        """失败保持待判别并留错误，下一次可续跑；不会伪造结果或回退规则。"""
        pending: dict[str, list[TriageInput]] = {}
        for item in self.store.inputs(batch_id):
            cached = self.store.cached(self.identity(item))
            if cached:
                self.store.finish(batch_id, item, cached)
            else:
                pending.setdefault(self.identity(item), []).append(item)
        groups = list(pending.values())
        for start in range(0, len(groups), 16):
            selected_groups = groups[start : start + 16]
            items = [group[0] for group in selected_groups]
            try:
                results = self.model.classify(items, self.policy)
                if len(results) != len(items):
                    raise ValueError("判别输出数量错误")
            except Exception as exc:
                for group in selected_groups:
                    for item in group:
                        self.store.fail(batch_id, item, "本地判别失败：" + type(exc).__name__)
                raise RuntimeError("本地判别未完成，记录已保留，可续跑") from None
            for group, result in zip(selected_groups, results, strict=True):
                decision = self.decision(group[0], result)
                for item in group:
                    self.store.finish(batch_id, item, decision)

    def decision(self, item: TriageInput, result: ModelDecision) -> TriageDecision:
        """新版只有是否进入；并列时不进入，旧三分类仅保留历史回放语义。"""
        if set(self.policy.criteria) == {"analyze", "exclude"}:
            if result.probabilities.insufficient is not None or result.choice == "insufficient":
                raise ValueError("二分类任务不能提交第三选项")
            admitted = result.probabilities.analyze > result.probabilities.exclude
            route = "analyze" if admitted else "exclude"
            reason = "二分类：进入详情分析" if admitted else "二分类：不进入详情分析"
            if result.probabilities.analyze == result.probabilities.exclude:
                reason += "；同概率按不进入处理"
        else:
            route, reason = "needs_evidence", "历史三分类策略：保留原有补证路由"
            if result.choice == "analyze":
                route = "analyze"
            elif result.choice == "exclude" and result.probabilities.exclude >= (
                self.policy.reject_probability or 1
            ):
                route = "exclude"
        return TriageDecision(
            id=self.identity(item),
            model_revision=self.revision,
            policy_hash=self.policy.content_hash(),
            input_hash=item.content_hash(),
            input=item,
            result=result,
            route=route,
            routing_reason=reason,
            created_at=utcnow(),
        )
