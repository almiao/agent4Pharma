"""冻结技能资源的运行契约；只按白名单身份读取，不解析模型提供的文件路径。"""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillResource(BaseModel):
    """一个不可变技能文件；hash 校验内容一致性，不证明方法质量。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-zA-Z0-9_./-]+$")
    text: str = Field(min_length=1, max_length=24000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_content(self) -> "SkillResource":
        """拒绝路径逃逸形式与内容被改动的资源。"""
        if self.name.startswith("/") or any(part in {"", ".", ".."} for part in self.name.split("/")):
            raise ValueError("技能资源名必须是规范的包内相对身份")
        if hashlib.sha256(self.text.encode()).hexdigest() != self.sha256:
            raise ValueError("技能资源 hash 不匹配")
        return self


class SkillDescription(BaseModel):
    """发现阶段只加载名称、用途和资源目录，不倾倒正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1)
    entrypoint: str
    resources: tuple[str, ...]


class SkillBundle(BaseModel):
    """任务绑定的技能目录与完整资源快照；历史重试不访问磁盘当前版本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    outline_policy: Literal["", "news-outline-v1"] = Field(default="", exclude_if=lambda v: not v)
    context_policy: Literal[
        "", "research-checkpoint-v1", "research-checkpoint-v2", "stage-working-set-v1"
    ] = Field(default="", exclude_if=lambda v: not v)
    skills: tuple[SkillDescription, ...]
    resources: tuple[SkillResource, ...]

    @model_validator(mode="after")
    def verify_catalog(self) -> "SkillBundle":
        """目录不得重名、引用缺失文件或容纳无上限资源。"""
        names = {r.name for r in self.resources}
        if len(names) != len(self.resources) or len({s.name for s in self.skills}) != len(self.skills):
            raise ValueError("技能或资源身份重复")
        if len(self.resources) > 100 or sum(len(r.text) for r in self.resources) > 240000:
            raise ValueError("技能包超过冻结额度")
        for skill in self.skills:
            if skill.entrypoint not in skill.resources or not set(skill.resources) <= names:
                raise ValueError("技能目录引用缺失资源")
        return self

    @property
    def content_hash(self) -> str:
        """包含目录和正文的稳定身份；任一引用规则变化均改变身份。"""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def read(self, name: str) -> SkillResource:
        """精确白名单读取完整的有界文件；不接受任意磁盘路径。"""
        for resource in self.resources:
            if resource.name == name:
                return resource
        raise ValueError("资源不属于本任务冻结技能包")


class SkillCatalogView(BaseModel):
    """提供给模型的渐进加载目录。"""

    version: str
    content_hash: str
    skills: tuple[SkillDescription, ...]
