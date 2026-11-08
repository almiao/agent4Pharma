"""生成显式绑定角色任务的 DSH 配置，关闭通用 Shell 和文件编辑能力。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent4pharma.runtime.context_window_policy import RevisionWindowPolicy
from agent4pharma.runtime.settings import ApplicationSettings


def create_patch(
    config_path: Path,
    config: ApplicationSettings,
    task_id: str,
    prompt: str,
    *,
    mcp_args: list[str],
    mcp_cwd: Path | None = None,
    tool_timeout_ms: int | None = None,
    web_search: bool = False,
    newsroom_context: bool = False,
    research_context: bool = False,
    sharp_native: Path | None = None,
    revision_window_policy: RevisionWindowPolicy | None = None,
    required_tool_names: tuple[str, ...] = (),
) -> Path:
    # JSON is valid YAML. No credentials are serialized into the Cordis patch.
    """生成角色任务的 DSH/MCP 配置；调用者必须给出工具入口，不序列化凭证。"""
    if not mcp_args:
        raise ValueError("必须明确指定任务 MCP 入口")
    rows = [
        {"id": "persistent-bash", "name": "@deepseek-ai/dsh-tool-bash-persistent", "disabled": True},
        {"id": "persistent-pwsh", "name": "@deepseek-ai/dsh-tool-pwsh-persistent", "disabled": True},
        {
            "id": "tool-fs",
            "name": "@deepseek-ai/dsh-tool-fs",
            "config": {"read": False, "readImage": False, "write": False, "edit": False},
        },
        {
            "id": "system-prompt",
            "name": "@deepseek-ai/dsh-system-prompt",
            "config": {"personaPrefix": prompt},
        },
        {
            "id": "pharma-mcp",
            "name": "@deepseek-ai/dsh-mcp-client",
            "config": {
                "serverName": "pharma",
                "transport": "stdio",
                "command": sys.executable,
                "args": mcp_args,
                "cwd": str((mcp_cwd or config_path.resolve().parent.parent).resolve()),
                **({"toolCallTimeoutMs": tool_timeout_ms} if tool_timeout_ms is not None else {}),
                "failOnStartupError": True,
            },
        },
    ]
    # 覆盖层中不存在的 id 只会被忽略；新 MCP 客户端必须使用显式 insert。
    rows[-1] = {"insert": [rows[-1]]}
    if sharp_native is not None:
        rows.append(
            {
                "insert": [
                    {
                        "id": "pharma-sharp-native",
                        "name": Path(__file__).with_name("dsh_sharp_native.cjs").resolve().as_uri(),
                    }
                ]
            }
        )
    if newsroom_context:
        rows.append(
            {
                "id": "compaction-basic",
                "config": {
                    "thresholdRatio": 0.06 if research_context else 0.6,
                    "retainRatio": 0.02 if research_context else 0.12,
                    "headroomTokens": 16384,
                    "maxTokens": 4096,
                },
            }
        )
    if web_search:
        rows.append(
            {
                "insert": [
                    {
                        "id": "newsroom-web",
                        "name": "@deepseek-ai/dsh-web",
                        "config": {"searchProvider": "deepseek-official"},
                    },
                    {
                        "id": "newsroom-search",
                        "name": "@deepseek-ai/dsh-web-search-deepseek",
                        "config": {
                            "apiKeyEnv": "DEEPSEEK_API_KEY",
                            "model": "deepseek-flash",
                            "maxTokens": 2048,
                            "maxUses": 2,
                        },
                    },
                    {
                        "id": "newsroom-web-tools",
                        "name": "@deepseek-ai/dsh-tool-web",
                        "config": {
                            "search": True,
                            "fetch": False,
                            "searchMaxQueries": 1,
                            "searchMaxResults": 5,
                            "searchSummaryMaxChars": 800,
                            "searchTimeoutMs": 60000,
                        },
                    },
                    {"id": "newsroom-tool-timeout", "name": "@deepseek-ai/dsh-tool-call-timeout-policy"},
                ]
            }
        )
    if required_tool_names:
        rows.append({"id": "tool-working-directory", "disabled": True})
        rows.append(
            {
                "insert": [
                    {
                        "id": "pharma-tool-readiness",
                        "name": Path(__file__).with_name("dsh_tool_readiness.cjs").resolve().as_uri(),
                        "config": {"required": required_tool_names},
                    }
                ]
            }
        )
    if revision_window_policy:
        rows.append({"id": "compaction-basic", "config": {"auto": False, "maxTokens": 4096}})
        rows.append(
            {
                "insert": [
                    {
                        "id": "pharma-revision-window",
                        "name": Path(__file__).with_name("dsh_revision_window.cjs").resolve().as_uri(),
                        "config": revision_window_policy.model_dump(),
                    }
                ]
            }
        )
    root = config.data_dir / "agent"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{task_id}.patch.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
