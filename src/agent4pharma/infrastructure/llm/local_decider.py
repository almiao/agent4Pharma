"""回环地址 Decider 原生判别协议适配，不使用 Chat Completions 或远程回退。"""

from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, FiniteFloat

from agent4pharma.modules.discovery.triage_contracts import ModelDecision, TriageInput, TriagePolicy


class DeciderIdentity(BaseModel):
    """本地服务的真实模型身份，调用前和结果返回时均需核对。"""

    model: str
    revision: str
    device: str
    attention: str


class DecisionAnswer(BaseModel):
    """官方 choice 答案，不将概率解释为生成式理由。"""

    choice: str
    probabilities: dict[str, float]
    confidence: float


class NativeResponse(BaseModel):
    """原生协议单篇输出。"""

    answers: dict[str, DecisionAnswer]
    usage: dict[str, int]


class DeciderResponse(BaseModel):
    """有界批处理响应，所有文章必须对应一个答案。"""

    identity: DeciderIdentity
    responses: list[NativeResponse] = Field(max_length=16)


class RankingResponse(BaseModel):
    """原生 noul 问题的 true-false 分值差，严格禁止非有限数。"""

    identity: DeciderIdentity
    margins: list[FiniteFloat] = Field(min_length=1, max_length=16)


class LocalDecider:
    """仅通过已配置的本机服务推理，不读取模型凭证或浏览器状态。"""

    def __init__(self, endpoint: str, revision: str, timeout: float = 120):
        parts = urlsplit(endpoint)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("本地判别只允许回环 HTTP 服务")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("本地判别地址不允许凭证或查询参数")
        self.endpoint, self.revision, self.timeout = endpoint.rstrip("/"), revision, timeout

    def classify(self, inputs: list[TriageInput], policy: TriagePolicy) -> list[ModelDecision]:
        """一次最多 16 篇、每篇一个独立问题；严格核对版本、数量和概率。"""
        requests = [
            {
                "state": item.model_dump(exclude={"material_ref", "source_record_id", "source_id", "url"}),
                "questions": {
                    "route": {
                        "type": "choice",
                        "instructions": policy.instructions,
                        "criteria": policy.criteria,
                    }
                },
            }
            for item in inputs
        ]
        try:
            with httpx.Client(trust_env=False, timeout=self.timeout) as client:
                response = client.post(self.endpoint + "/decide", json={"requests": requests})
                response.raise_for_status()
            payload = DeciderResponse.model_validate_json(response.content)
        except httpx.HTTPError as exc:
            raise RuntimeError("本地 Decider 请求失败：" + type(exc).__name__) from None
        if (
            payload.identity.revision != self.revision
            or payload.identity.model != "perplexity-ai/pplx-decider-flash-v1.2"
            or payload.identity.attention != "noncausal_full_attention"
        ):
            raise ValueError("本地模型版本或注意力协议不匹配")
        if len(payload.responses) != len(inputs):
            raise ValueError("本地模型结果数量与输入不一致")
        results = []
        for response in payload.responses:
            answer = response.answers["route"]
            if set(answer.probabilities) != set(policy.criteria):
                raise ValueError("本地模型选项不匹配")
            if abs(sum(answer.probabilities.values()) - 1) > 1e-4:
                raise ValueError("本地模型概率不归一")
            result = ModelDecision(
                choice=answer.choice,
                probabilities=answer.probabilities,
                input_tokens=response.usage["input_tokens"],
            )
            if result.choice != max(answer.probabilities, key=answer.probabilities.get):
                raise ValueError("本地模型选项不是最大概率项")
            results.append(result)
        return results

    def rank(self, inputs: list[TriageInput], instructions: str) -> list[float]:
        """批量独立计算编辑优先阅读分，不复用分类概率，不调用商业模型。"""
        if not 1 <= len(inputs) <= 16:
            raise ValueError("本地排序每批最多16篇")
        requests = [
            {
                "state": item.model_dump(exclude={"material_ref", "source_record_id", "source_id", "url"}),
                "questions": {
                    "priority": {
                        "type": "noul",
                        "instructions": instructions,
                        "criteria": {"false": "不优先深入阅读", "true": "优先深入阅读"},
                    }
                },
            }
            for item in inputs
        ]
        try:
            with httpx.Client(trust_env=False, timeout=self.timeout) as client:
                response = client.post(self.endpoint + "/rank", json={"requests": requests})
                response.raise_for_status()
            payload = RankingResponse.model_validate_json(response.content)
        except httpx.HTTPError as exc:
            raise RuntimeError("本地排序失败：" + type(exc).__name__) from None
        if (
            payload.identity.revision != self.revision
            or payload.identity.model != "perplexity-ai/pplx-decider-flash-v1.2"
            or payload.identity.attention != "noncausal_full_attention"
        ):
            raise ValueError("本地排序模型身份不匹配")
        if len(payload.margins) != len(inputs):
            raise ValueError("本地排序结果数量不匹配")
        return payload.margins
