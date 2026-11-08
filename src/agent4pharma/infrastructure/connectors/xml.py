"""外部 XML 解析边界：将解析和来源错误转换为安全异常，规范化元素文本。"""

import xml.etree.ElementTree as ET

from agent4pharma.modules.collection.errors import SourceError
from agent4pharma.modules.materials.collection_records import SourceResponse


def xml(raw: SourceResponse) -> ET.Element:
    """解析外部 XML；格式错误或来源错误节点转换为不含响应正文的 SourceError。"""
    try:
        root = ET.fromstring(raw.body)
    except ET.ParseError as exc:
        raise SourceError(f"Invalid source XML: {exc.code}") from None
    if root.find(".//ERROR") is not None:
        raise SourceError("Source returned an XML error")
    return root


def text(element: ET.Element | None) -> str:
    """合并元素文本并规范化空白；元素不存在时返回空字符串。"""
    return " ".join("".join(element.itertext()).split()) if element is not None else ""
