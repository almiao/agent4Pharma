"""筛选运行目录的只读契约；历史列表不加载完整领域对象。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperSelectionSummary:
    """准确运行身份和所属批次；不含论文池、提示词或正文。"""

    id: str
    batch_id: str
    created_at: str
    phase: str
