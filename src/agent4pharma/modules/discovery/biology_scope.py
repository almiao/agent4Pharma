"""AI × Biology/Pharma 新范围的版本化策略工厂；不重释旧策略或历史评估字段。"""

from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy


def biology_screening_policy() -> ScreeningPolicy:
    """编译上游检索式的方法/领域词表；已采集资料统一交本地 Decider 判别。"""
    old = ScreeningPolicy()
    return ScreeningPolicy(
        name="AI × Biology/Pharma v1",
        scope_version="ai-biology-v1",
        agent_terms=old.agent_terms,
        model_terms=old.model_terms
        + [
            "artificial intelligence",
            "machine learning",
            "deep learning",
            "foundation model",
            "protein language model",
            "medical superintelligence",
            "AI",
            "ML",
        ],
        drug_terms=old.drug_terms
        + [
            "biology",
            "biological",
            "protein",
            "molecular",
            "genomics",
            "omics",
            "single cell",
            "virtual cell",
            "RNA",
            "DNA",
            "enzyme",
            "antibody",
            "healthcare",
            "medical",
            "clinical",
            "patient",
            "laboratory",
            "生物",
            "蛋白",
            "组学",
            "医学",
            "临床",
        ],
        review_terms=[],
        scope="AI × Biology/Pharma，包含蛋白/分子基础模型、传统 ML 生物研究与医学转化。",
        notes="词表用于编译来源查询和人工预览；已采集资料交本地 Decider 二分类。",
    )
