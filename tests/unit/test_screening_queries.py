"""粗筛查询渲染：PubMed 排除二手文献类型，arXiv 检索式保持原样。"""

from agent4pharma.infrastructure.connectors.screening_queries import (
    PUBMED_EXCLUDED_TYPES,
    render_queries,
)
from agent4pharma.modules.discovery.biology_scope import biology_screening_policy


def test_pubmed_query_excludes_secondary_publication_types():
    query = render_queries(biology_screening_policy())["pubmed"]
    assert "[Title/Abstract]" in query
    assert query.count("[Publication Type]") == len(PUBMED_EXCLUDED_TYPES)
    for kind in PUBMED_EXCLUDED_TYPES:
        assert f'"{kind}"[Publication Type]' in query
    # 主题分支先合并成一个整体，再整体取反，避免 NOT 只作用于最后一个 OR 分支。
    assert " AND " in query.split(") NOT (")[0]
    assert query.endswith(")")


def test_arxiv_query_keeps_ti_abs_and_no_pubmed_filters():
    query = render_queries(biology_screening_policy())["arxiv"]
    assert "[Publication Type]" not in query
    assert "ti:" in query and "abs:" in query
    assert " NOT " not in query
