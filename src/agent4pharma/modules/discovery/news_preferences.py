"""新闻筛选的显式机构关注名单与可审阅匹配；不是机构质量认证或主题词过滤。"""

import re
import unicodedata
from functools import lru_cache

from pydantic import BaseModel, Field


class InstitutionPreference(BaseModel):
    """编辑优先关注的机构及明确别名，随筛选运行冻结。"""

    name: str
    aliases: list[str] = Field(min_length=1)


def default_institution_preferences() -> list[InstitutionPreference]:
    """提供初始编辑关注名单；未列入不代表低质量，短而歧义的缩写不匹配。"""
    aliases = {
        "Google DeepMind": ["Google DeepMind", "DeepMind"],
        "Google": ["Google Research", "Google LLC"],
        "Microsoft": ["Microsoft", "微软"],
        "NVIDIA": ["NVIDIA", "英伟达"],
        "Meta": ["Meta AI", "Meta Platforms", "FAIR, Meta"],
        "OpenAI": ["OpenAI"],
        "Isomorphic Labs": ["Isomorphic Labs"],
        "Insilico Medicine": ["Insilico Medicine", "英矽智能"],
        "Recursion": ["Recursion Pharmaceuticals", "Recursion Therapeutics"],
        "Xaira": ["Xaira Therapeutics"],
        "Iambic": ["Iambic Therapeutics"],
        "Generate Biomedicines": ["Generate Biomedicines"],
        "EvolutionaryScale": ["EvolutionaryScale"],
        "Chai Discovery": ["Chai Discovery"],
        "XtalPi": ["XtalPi", "晶泰科技"],
        "Arc Institute": ["Arc Institute"],
        "Broad Institute": ["Broad Institute"],
        "MIT": ["Massachusetts Institute of Technology", "MIT", "麻省理工"],
        "Harvard": ["Harvard", "哈佛大学"],
        "Stanford": ["Stanford", "斯坦福大学"],
        "Oxford": ["University of Oxford", "Oxford University", "牛津大学"],
        "Cambridge": ["University of Cambridge", "Cambridge University", "剑桥大学"],
        "Chinese Academy of Sciences": ["Chinese Academy of Sciences", "中国科学院"],
        "Tsinghua": ["Tsinghua", "清华大学"],
        "Peking University": ["Peking University", "北京大学"],
    }
    return [InstitutionPreference(name=name, aliases=values) for name, values in aliases.items()]


@lru_cache(maxsize=512)
def _alias_pattern(alias: str) -> tuple[str, re.Pattern[str]]:
    value = unicodedata.normalize("NFKC", alias).casefold()
    pattern = re.escape(value)
    if value.isascii():
        pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
    return value, re.compile(pattern)


def match_priority_institutions(
    affiliations: list[str], preferences: list[InstitutionPreference]
) -> list[str]:
    """只匹配明确作者单位，词边界避免 MIT 命中 Smith；不从正文提及或作者名猜测归属。"""
    corpus = unicodedata.normalize("NFKC", "\n".join(affiliations)).casefold()
    matched = []
    for preference in preferences:
        for alias in preference.aliases:
            value, pattern = _alias_pattern(alias)
            if value in corpus and pattern.search(corpus):
                matched.append(preference.name)
                break
    return matched
