"""人工定稿绑定完整渠道包；人工编辑由人负责最终判断，不授权外部发送。"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from agent4pharma.modules.publishing.release_contracts import ChannelPackage


class ApprovePackage(BaseModel):
    """浏览者确认的准确包指纹；并发变化由应用用例拒绝。"""

    model_config = ConfigDict(extra="forbid")
    package_id: str
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=5, max_length=2000)


class HumanApproval(BaseModel):
    """不可变人工批准记录；相同包内容重复提交复用同一记录。"""

    id: str
    package_id: str
    story_id: str
    fingerprint: str
    actor: str
    reason: str
    created_at: str


def package_fingerprint(package: ChannelPackage) -> str:
    """正文、排版、配图、核验引用和版本共同组成批准身份。"""
    return hashlib.sha256(package.model_dump_json().encode()).hexdigest()
