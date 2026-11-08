"""按已支持的来源键构造连接器，所有连接器共用注入的 HTTP 传输实例。"""

from agent4pharma.infrastructure.connectors.arxiv import Arxiv
from agent4pharma.infrastructure.connectors.pubmed import PubMed
from agent4pharma.infrastructure.connectors.rxiv import Rxiv
from agent4pharma.infrastructure.connectors.transport import Transport
from agent4pharma.modules.collection.acquisition_interfaces import SourceConnector


def connectors(transport: Transport) -> dict[str, SourceConnector]:
    """构造四来源连接器映射，共享传入的 transport，不创建独立网络会话。"""
    return {
        "pubmed": PubMed(transport),
        "arxiv": Arxiv(transport),
        "biorxiv": Rxiv("biorxiv", transport),
        "medrxiv": Rxiv("medrxiv", transport),
    }
