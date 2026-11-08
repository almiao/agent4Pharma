# 本项目安装的外部技能

安装日期：2026-10-30。使用 Codex skill-installer 从固定 GitHub 提交安装；[安装清单](skill-installation.json)记录来源、文件 SHA256、验证状态与本地调整。技能目录为 `.agents/skills`，本项目下一轮对话可发现这些技能。

## 可用范围

| 技能 | 当前验证 | 使用边界 |
|---|---|---|
| frontend-design | SKILL.md 与 LICENSE.txt 齐全 | 设计指导，不是已实现的页面或生成器 |
| ui-ux-pro-max | 离线产品检索与设计系统生成成功 | 脚本调用路径已改为实际安装绝对路径；移动项目后需重新定位。建议输出必须结合中文内容评审 |
| literature-review | 文件齐全、Python 语法检查通过 | kernel.py 依赖 host SDK；未适配、未运行真实检索 |
| pdf-explore | 文件齐全、Python 语法检查通过 | host SDK、PDF 库与 Claude 模型相关流程未验证，不因安装授权付费调用 |
| paper-narrative | 文件齐全、Python 语法检查通过 | host SDK/产物引用相关流程未适配 |
| figure-composer | 文件齐全、Python 语法检查通过 | host SDK、多面板任务与审阅流程未适配 |
| figure-style | 文件齐全、Python 语法检查通过 | matplotlib/numpy/scipy 等运行依赖及渲染尚未验证 |

后五项来自 BioTender 汇编的 claude-science 分类，为第三方整理，不是 Anthropic 官方发布给 Codex 的技能包。不要把安装记录当作论文、模型、图表或付费工作流的真实验证。现有项目的 DSH、权限、预算和渠道契约继续生效；不为适配这些技能替换项目运行底座。

## 调用示例

从项目根目录运行 UI UX Pro Max 的本地检索：

```powershell
uv run python .agents/skills/ui-ux-pro-max/scripts/search.py "editorial magazine" --domain product -n 1 --json
uv run python .agents/skills/ui-ux-pro-max/scripts/search.py "editorial magazine" --design-system -p agent4Pharma -f markdown
```

上述调用只读取本地资源并打印建议。本次没有使用 `--persist`，没有让生成器写入正式视觉规范。当前正式设计仍未定稿；以后应维护唯一规范来源，避免生成的 MASTER.md 与 DESIGN.md 冲突。

frontend-design 随目录保留 Apache-2.0 许可；UI UX Pro Max 随目录保留 MIT 许可。BioTender 汇编根许可保存在 bio-collection-LICENSE；五个 claude-science 技能各自声明 Apache-2.0，对应许可文本保存在 Apache-2.0.txt。汇编根 CC0 不代表所有上游素材和依赖均为 CC0。

安装器曾因 Windows 权限拒绝无法写入全局技能目录，之后成功安装到本项目；没有修改全局目录 ACL，没有覆盖已有技能。
