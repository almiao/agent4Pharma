"""商业模型的有界 JSON 新闻比较；不装配联网工具、Shell 或全文读取能力。"""

import json
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from agent4pharma.modules.discovery.news_selection import (
    NewsComparisonPaper,
    NewsComparisonRequest,
    NewsComparisonRequestError,
    NewsComparisonResult,
    NewsComparisonValidationError,
)


class _Message(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _Message
    finish_reason: str


class _Response(BaseModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)


_QUOTE_FIELDS = ("evidence_quote", "ai_quote", "domain_quote", "institution_quote")
_REFERENCE_CONTRACT = (
    "\n程序引用协议 evidence-ref-v1：本次papers.quote_spans列出各篇实际原文片段。"
    "evidence_quote、ai_quote、domain_quote、institution_quote字段只能填写本篇quote_spans的id，"
    "无需引用时填空字符串；禁止填写原文、改写、拼接或其他论文的编号。"
    "程序将编号还原成原文并严格校验。recommend的前三个引用字段必须有本篇有效编号。"
    "title/source_text是可用于前三项的正文依据；affiliation仅可用于institution_quote。"
    "同一片段确实支持多个判断时可以重复选择；引用可定位不代表判断已被独立核实。"
)

_COMPACT_REFERENCE_CONTRACT = "\nevidence_quote 只填本篇 quote_spans 的编号。程序会还原原文。"


def _quote_spans(
    alias: str, paper: NewsComparisonPaper, *, include_affiliations: bool = True
) -> dict[str, str]:
    """建立有界原文引用片段；不清洗、改写或借用其他论文内容。"""
    spans = {}
    sources = [("t", paper.title), ("s", paper.text)]
    if include_affiliations:
        sources.extend((f"a{index}", text) for index, text in enumerate(paper.affiliations, 1))
    for kind, text in sources:
        for index, start in enumerate(range(0, len(text), 600), 1):
            value = text[start : start + 600]
            if value.strip():
                spans[f"{alias}.{kind}.{index}"] = value
    return spans


@dataclass
class CommercialNewsComparison:
    """使用启动边界注入的凭证，按冻结模型请求执行一次；调用方负责有限重试。"""

    protocol_version: ClassVar[str] = "short-id+evidence-ref-v1"
    api_key: str
    timeout_seconds: float = 180

    def compare(self, request: NewsComparisonRequest) -> NewsComparisonResult:
        """只发送冻结池，校验 JSON 协议；错误不包含凭证、响应正文或连接地址。"""
        endpoint = urlsplit(request.endpoint)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.netloc
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("商业模型端点无效")
        if not self.api_key:
            raise ValueError("商业新闻比较缺少凭证")
        # 长哈希不是编辑判断所需内容。传输使用一一对应短ID，原冻结输入和持久化身份不变。
        identities = {f"p{index}": paper.record_id for index, paper in enumerate(request.papers, 1)}
        compact = request.uses_compact_output
        reference_contract = _COMPACT_REFERENCE_CONTRACT if compact else _REFERENCE_CONTRACT
        spans = {
            alias: _quote_spans(alias, paper, include_affiliations=not compact)
            for alias, paper in zip(identities, request.papers, strict=True)
        }
        papers = [
            paper.model_dump(mode="json") | {"record_id": alias, "quote_spans": spans[alias]}
            for alias, paper in zip(identities, request.papers, strict=True)
        ]
        try:
            with httpx.Client(trust_env=False, timeout=min(180, self.timeout_seconds)) as client:
                response = client.post(
                    request.endpoint.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key},
                    json={
                        "model": request.model,
                        "max_tokens": request.max_tokens,
                        "response_format": {"type": "json_object"},
                        **({"thinking": {"type": "disabled"}} if request.thinking_mode == "disabled" else {}),
                        "messages": [
                            {"role": "system", "content": request.instructions + reference_contract},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "limit": request.limit,
                                        "required_card_count": len(request.papers),
                                        "required_card_ids": list(identities),
                                        "selected_ids_unique": True,
                                        "papers": papers,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                    },
                )
                response.raise_for_status()
            answer = _Response.model_validate_json(response.content).choices[0]
            if answer.finish_reason != "stop":
                raise NewsComparisonRequestError("商业比较输出未完整结束，请重试本轮比较")
            result = NewsComparisonResult.model_validate_json(answer.message.content)
            returned = [*result.selected_ids, *(card.record_id for card in result.cards)]
            if any(identity not in identities for identity in returned):
                raise NewsComparisonValidationError("比较输出包含本轮未知论文ID，结果未采纳")
            # 只还原双射；不去重、不补卡片、不修改取舍，宿主随后照常严格校验。
            result.selected_ids = [identities[identity] for identity in result.selected_ids]
            for card in result.cards:
                visible = spans[card.record_id]
                if compact:
                    card.ai_quote = card.domain_quote = card.institution_quote = (
                        card.verification_question
                    ) = ""
                for field in _QUOTE_FIELDS:
                    reference = getattr(card, field)
                    if reference and reference not in visible:
                        raise NewsComparisonValidationError("比较引用编号不存在或属于其他论文，结果未采纳")
                    setattr(card, field, visible.get(reference, ""))
                card.record_id = identities[card.record_id]
            return result
        except httpx.HTTPStatusError as exc:
            raise NewsComparisonRequestError(
                "商业比较接口返回 HTTP " + str(exc.response.status_code)
            ) from None
        except httpx.HTTPError as exc:
            raise NewsComparisonRequestError("商业比较连接失败（" + type(exc).__name__ + "）") from None
        except ValidationError as exc:
            raise NewsComparisonRequestError(
                "商业比较输出格式不符合协议（" + str(exc.error_count()) + "处字段或JSON错误），结果未采纳"
            ) from None
