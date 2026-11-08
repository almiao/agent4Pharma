"""采集业务可识别的安全异常：区分来源失败与需要重置游标的分页变化。"""


class SourceError(RuntimeError):
    """可安全记录的来源失败；异常文本不得包含凭证或原始鉴权响应。"""

    pass


class PaginationChanged(SourceError):
    """来源分页集合发生变化，需要重置游标后重新扫描窗口。"""

    pass
