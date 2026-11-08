# agent4Pharma

面向 AI × Biology/Pharma 的本地采编工作台，覆盖文献与新闻采集、论文预选与排序、人工推荐、研究写作、阅读评审、事实核验、图文制作和人工定稿。主题包括非语言生物基础模型，排除仅使用传统机器学习的研究；范围确认不等于上游召回已经全面覆盖。

正式后台使用 React + Ant Design，后端为 Python 模块化单体，提供 `pharma` CLI、本机 HTTP 工作台与受范围约束的 MCP 工具。当前能力与边界见 [功能状态表](docs/console-migration-matrix.md)。

## 新机器快速安装

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，再从仓库根目录执行：

```powershell
# Windows：安装锁定依赖与开发工具；缺少 Python 3.12 时由 uv 下载
# 创建 .env，不覆盖已有文件
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
uv run pharma workbench --port 8765
```

打开 [工作台](http://127.0.0.1:8765/console)。仓库保留随 Python 包分发的前端构建产物。
标准命令入口为 `pharma`，也可使用 `uv run python -m agent4pharma`；查看命令使用 `uv run pharma --help`。

其他系统或手动安装：

```sh
uv sync --locked --group dev --python 3.12
# 仅在 .env 不存在时复制模板，再在本地编辑
cp -n .env.example .env
uv run pharma workbench --port 8765
```

需要模型时，在本地 `.env` 填写 `DEEPSEEK_API_KEY`，可通过 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL` 覆盖模型和地址。
已有进程环境优先，真实凭证不要入库。安装依赖不调用模型，也不启动独立服务。

新克隆不包含本机数据库、已有来源配置、资料、历史任务或模型权重。MinerU、Decider、浏览器扩展与下载运行时需另行配置；基础安装完成不表示完整采编服务已就绪。
完整依赖清单、独立服务与 GitHub 提交范围见 [安装与仓库准备](docs/installation.md)。

## 前端开发

先安装 Node.js 24，并让 node 与 npm 来自同一安装。在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -BuildFrontend
```

此命令额外按前端锁文件安装依赖并重建 `src/agent4pharma/entrypoints/web/static/live-console/`。
该目录需要提交，随 Python 包分发。仅运行打包后的工作台不需要重建前端；组件职责与开发方式见 [前端说明](frontend/README.md)。

## 运行与数据边界

- 工作台启动、重做、继续和重试即请求模型执行；浏览、刷新、配置保存与发布不授权执行。业务操作见 [生产说明](docs/production-user-guide.md)。
- 论文从本地预选与排序进入商业模型整体比较，再由人工推荐并取得主文；不恢复旧逐篇精筛或随机抽样。见 [论文流程](docs/paper-selection-pipeline.md)与 [本地排序](docs/local-rss-triage.md)。
- MinerU 解析、Decider 推理及浏览器连接需要明确准备并启动对应依赖。正文读取与图片解析见 [MinerU 接入](docs/mineru-provider.md)。
- 人工定稿绑定准确图文版本；批准后可导出或交付公众号草稿箱，草稿箱交付不等于公开发布。见 [图文制作](docs/publishing-layout.md)。
- 数据库、资料、报告、模型、浏览器状态与服务工作目录位于忽略目录 `data/`。文件配置见 `config/`；数据库内配置不会随代码克隆迁移。
- HTTP 工作台面向本机单用户，尚无登录权限体系。来源覆盖、真实模型质量和服务可用性需分别验证。

## 开发检查

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/check_architecture.py
uv run pytest -q
```

默认测试离线、无付费调用；真实来源和模型冒烟需显式任务授权。离线通过不代表真实模型生成、OCR 或 GPU 推理已验证。

开发先读 [文档索引](docs/README.md)与 [功能状态表](docs/console-migration-matrix.md)，再按任务阅读专题契约。
工程边界见 [代码架构](docs/code-architecture.md)、[模块布局](docs/refactor-layout.md)和 [有效架构决策](docs/architecture-decisions.md)；开发约定见 [AGENTS.md](AGENTS.md)。
