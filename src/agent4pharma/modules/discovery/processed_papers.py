"""以明确资料/DOI/来源版本身份避开已经成功关联主文的论文，不猜测标题相似性。"""

from collections.abc import Sequence

from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceRequest
from agent4pharma.modules.materials.material_identity import identity
from agent4pharma.modules.materials.paper_downloads import PaperDownloadResult


def _keys(material_ref: str, record_id: str, doi: str) -> set[str]:
    keys = {"material:" + material_ref, "record:" + record_id}
    if doi.strip():
        keys.add(identity("", "", {"doi": doi}))
    return keys - {"material:", "record:"}


def paper_keys(request: IntelligenceRequest) -> set[str]:
    """返回冻结请求的精确身份键；DOI 跨来源、跨版本匹配。"""
    primary = request.evidence[0]
    return _keys(request.material_ref, primary.id, primary.doi)


def processed_paper_keys(attempts: Sequence[PaperDownloadResult]) -> set[str]:
    """仅将成功且主文身份通过的正式关联计为已处理；失败、调试不阻断后续批次。"""
    keys: set[str] = set()
    for result in attempts:
        request = result.request
        if (
            result.status == "downloaded"
            and result.identity_match
            and result.sha256
            and result.pages > 0
            and request.decision_id.startswith("selection-")
        ):
            keys.update(_keys(request.material_ref, request.source_record_id, request.identifier))
    return keys
