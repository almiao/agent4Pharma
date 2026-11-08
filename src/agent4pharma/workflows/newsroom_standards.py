"""公共编辑规范的资源映射与阶段用途；正文及发布版本由配置仓储拥有。"""

from typing import NamedTuple

from agent4pharma.modules.model_configuration.prompt_contracts import PromptKey, PromptVersion


class EditorialStandard(NamedTuple):
    """一个受控的可编辑规范及其执行用途，不允许配置任意资源路径。"""

    key: PromptKey
    title: str
    resource: str
    purpose: str
    exposure: tuple[tuple[str, str], ...]


EDITORIAL_STANDARDS = (
    EditorialStandard(
        "audience",
        "目标读者规范",
        "standards/audience.md",
        "统一媒体读者、知识假设及阅读收益",
        (
            ("主题初筛", "不注入分类指令；专业概念多不作为排除理由"),
            ("价值排序 / 论文精选", "读取统一定位，判断跨专业阅读收益；不改变主题与机构排序规则"),
            ("论文理解", "解释核心问题与陌生概念，准确记录数据与条件供底稿使用，不要求全部写入正文"),
            ("背景调查 / 选题补查", "只补理解主线及判断新闻价值所必需的背景"),
            ("主编立项", "沿用统一受众；reader只补需解释的概念与背景，不新增专业知识前提"),
            (
                "稿件写作 / 协作编辑",
                "面向无相关专业背景的读者，先讲清问题与发现，准确转述论文，不追加防卫性声明",
            ),
            ("编辑核验", "检查稿件是否准确转述，不核验论文的科学有效性；表达偏好仅为建议"),
            ("排版交付", "保留已审定解释、图注和限定，不重新定义受众"),
        ),
    ),
    EditorialStandard(
        "scientific_evidence",
        "科研证据规范",
        "standards/scientific-evidence.md",
        "约束事实归属、证据支持与结论范围",
        (("制作各阶段 / 协作编辑", "核对稿件与原文的一致性，不审查论文或要求防卫性陈述"),),
    ),
    EditorialStandard(
        "chinese_news_style",
        "中文表达规范",
        "standards/chinese-news-style.md",
        "统一中文标题、术语、句段与表达品质",
        (("稿件写作 / 编辑核验 / 协作编辑", "组织必要解释，检查语言与术语一致性"),),
    ),
)


def standard_resource_text(version: PromptVersion) -> str:
    """将真实配置身份与正文共同冻结，供执行追溯且不增加模型调用。"""
    return f"<!-- 规范版本：{version.id}；hash={version.content_hash} -->\n{version.text()}"


def selection_audience_instructions(version: PromptVersion) -> str:
    """给价值判断提供冻结受众，不改变主题门槛、优先级与输出契约。"""
    return (
        "\n\n以下目标读者规范只用于判断新闻价值与阅读收益，不改变主题纳入、期刊/机构优先级、"
        "容量、引用和输出结构；不要因细分术语多而排除，也不把细分同行兴趣作为唯一阅读收益。\n"
        + standard_resource_text(version)
    )


def selection_prompt_instructions(version: PromptVersion) -> str:
    """冻结价值排序或整体比较的提示词身份、正文与示例，供新运行追溯。"""
    return f"<!-- 提示词版本：{version.id}；hash={version.content_hash} -->\n{version.text()}"
