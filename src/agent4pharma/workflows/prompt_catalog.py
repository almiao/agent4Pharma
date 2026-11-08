"""按实际业务节点描述模型交互用途；不包含模型输出校验和权限策略。"""

from typing import NamedTuple

from agent4pharma.modules.model_configuration.prompt_contracts import PromptKey
from agent4pharma.workflows.newsroom_standards import EDITORIAL_STANDARDS


class PromptNode(NamedTuple):
    """配置目录中的业务节点、用途和动态输入说明。"""

    key: PromptKey
    title: str
    purpose: str
    inputs: str


PROMPT_NODES = (
    *(
        PromptNode(s.key, s.title, s.purpose, "；".join(stage for stage, _ in s.exposure))
        for s in EDITORIAL_STANDARDS
    ),
    PromptNode(
        "screening_scope",
        "共用主题标准",
        "统一资料预选的 AI × Biology/Pharma 纳入标准",
        "资料预选使用；发布时同步生成新版本",
    ),
    PromptNode("triage", "资料预选", "判断采集资料是否进入详情分析", "标题、来源摘要、主题标签和文章类型"),
    PromptNode(
        "news_ranking",
        "价值排序",
        "本地独立判断新闻价值，同档排序收敛前十",
        "单篇标题、摘要或来源文本、明确作者单位及目标读者规范",
    ),
    PromptNode(
        "news_comparison",
        "论文精选 · 整体比较",
        "一次比较冻结前十篇，提出最多五篇下载建议",
        "冻结前十篇资料、期刊、明确作者单位及目标读者规范",
    ),
    PromptNode("editor", "主编选题", "取舍候选并制定选题任务书", "候选摘要、证据和制作额度"),
    PromptNode(
        "writer", "内容写作", "按任务书和证据撰写或修改稿件", "选题任务书、实际证据、历史稿件与核验意见"
    ),
    PromptNode(
        "reviewer", "编辑核验", "核对准确稿件版本，给出问题与解决条件", "稿件、引用证据和历史核验记录"
    ),
    PromptNode("publisher", "发行归档", "将已通过核验的稿件整理为渠道包", "准确稿件、核验报告和渠道要求"),
    PromptNode(
        "maintenance", "离线调查", "调查来源、资料匹配与异常原因", "人工指定资料、原始来源及本地匹配结果"
    ),
)
