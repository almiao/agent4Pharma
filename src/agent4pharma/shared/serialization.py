"""Stable JSON encoding used in persisted identities and state."""

import json


def dump(value) -> str:
    """确定性编码 JSON，保留 Unicode 并排序键；不能编码的值直接抛错。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
