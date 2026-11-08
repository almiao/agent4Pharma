"""从冻结精选条目构造人工下载请求，不执行推荐判断。"""

from agent4pharma.modules.discovery.paper_selection import PaperSelectionItem, PaperSelectionRun
from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest


def download_request(run: PaperSelectionRun, item: PaperSelectionItem) -> PaperDownloadRequest:
    """只从冻结清单生成下载请求，不接受模型提供的新网址、身份或文件路径。"""
    primary = item.request.evidence[0]
    return PaperDownloadRequest(
        batch_id=run.batch_id,
        decision_id=run.id + ":" + primary.id,
        material_ref=item.request.material_ref,
        source_id=item.source_id,
        source_record_id=primary.id,
        title=primary.title,
        journal=item.journal,
        url=primary.url,
        identifier=primary.doi,
    )
