# 安装依赖与准备 GitHub 仓库

## 基础环境与一键安装

项目采用 Python 3.12+、uv 和独立的 React 前端。新机器建议使用 Python 3.12，与安装脚本一致。
先按 [uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv；Windows 可执行：

```powershell
winget install --id=astral-sh.uv -e
```

安装后重新打开终端，在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
uv run pharma workbench --port 8765
```

脚本用 `uv sync --locked --group dev --python 3.12` 建立 `.venv`，按锁文件安装运行依赖和开发工具。
缺少 Python 3.12 时 uv 可下载解释器；初次安装需要网络。锁文件与声明不一致时停止，避免无意更新依赖。
同步会移除环境中未声明的包，因此不要将 MinerU、torch 等另装进主项目 `.venv`。
脚本只在 `.env` 不存在时复制空模板，不读取或覆盖已有凭证，不启动业务或独立服务。
详见 [uv 锁定与同步说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

手动安装或其他系统：

```sh
uv sync --locked --group dev --python 3.12
# 仅 .env 不存在时复制，然后在本地编辑密钥
cp -n .env.example .env
uv run pharma workbench --port 8765
```

工作台地址为 `http://127.0.0.1:8765/console`。启动工作台不等于执行采集或模型任务。
需要模型时，在本地 `.env` 填写 `DEEPSEEK_API_KEY`；模板另支持模型及地址覆盖，加载规则见 `src/agent4pharma/runtime/environment.py`。
本机单用户 HTTP 服务没有登录权限体系，不应直接作为公网后台部署。

## 依赖分别由谁管理

| 范围 | 声明与锁文件 | 直接依赖/用途 |
|---|---|---|
| Python 运行 | `pyproject.toml` + `uv.lock` | httpx（HTTP）、pydantic（边界校验）、mcp（工具协议）、deepseek-harness-sdk（模型执行）、tzdata（时区）、beautifulsoup4（HTML）、cryptography（加密）、playwright（浏览器）、pymupdf（PDF） |
| Python 开发 | 同上，dev 组 | pytest、ruff |
| Python 构建 | `pyproject.toml` build-system | hatchling，由构建工具管理 |
| 前端 | `frontend/package.json` + `frontend/package-lock.json` | React、React DOM、Ant Design、lucide-react；Vite 构建 |
| 日常浏览器 MCP | `src/agent4pharma/infrastructure/web/browser_mcp_runtime/package*.json` | 锁定的 `@playwright/mcp`，单独安装到 `data/tools/browser-mcp` |

具体版本以声明和锁文件为准，不另手工维护 `requirements.txt`。三个范围的锁文件都应提交。
Python SDK 安装其声明的运行时；启动 DSH 不要求额外安装 Node。
修改前端或安装日常浏览器 MCP 时需要 Node/npm；仅运行已打包前端不需要重建。

## 前端开发

建议 Node.js 24；锁定的 Vite 要求 Node `^20.19.0 || >=22.12.0`。在根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -BuildFrontend
```

这会额外执行前端 `npm ci --include=optional` 与 `npm run build`。脚本通过当前 Node 调用 npm CLI，
避免 Windows 上 npm 启动脚本绑定旧版 Node；需要标准 Node/npm 安装布局。
构建目录为 `src/agent4pharma/entrypoints/web/static/live-console/`，它是随包分发的必需资源，应提交。
前端源码改动后同步重建；详细职责与测试见 [前端说明](../frontend/README.md)。

## 按功能另行准备的服务

| 能力 | 额外准备 | 说明 |
|---|---|---|
| 商业模型研究、写作、评审等 | DeepSeek API 凭证及网络 | 只在明确启动对应任务时调用，基础安装不调用模型 |
| 本地预选与排序 | 独立 Decider Python 环境、官方权重、兼容 CUDA GPU/驱动 | 当前配置启用 triage；完整筛选依赖此服务，主项目不安装 torch。步骤见 [本地排序](local-rss-triage.md) |
| 论文正文与图片解析 | 独立 MinerU CLI、已配置本地解析模型与服务 | 主环境不安装 MinerU；权重与服务需人工准备，见 [MinerU 接入](mineru-provider.md) |
| 使用日常浏览器下载 | Node/npm、Chrome、Playwright MCP 扩展及独立 MCP 包 | 在工作台按提示显式安装/连接，浏览器会话留在本机；见 [论文流程](paper-selection-pipeline.md) |

PyPI 中的 playwright 包不包含浏览器二进制。需要独立 Chromium 的路径可显式运行
`uv run playwright install chromium`；日常 Chrome 扩展连接仍需单独配置。
基础安装不会下载 Decider/MinerU 权重，不启动 GPU/OCR 服务，也不导入本机运行数据。
`config/discovery.toml` 是文件配置；数据库维护的来源、已发布规则及历史任务不会随 Git 克隆迁移。

## 哪些内容上传 GitHub

| 提交 | 忽略 |
|---|---|
| `src/`、`tests/`、`scripts/`、`config/`、`docs/`、README、AGENTS.md | `data/`（数据库、论文、导出包、模型、浏览器状态、日志等） |
| `pyproject.toml`、`uv.lock`、前端及浏览器 MCP 的 package/lock 文件 | `.venv/`、`node_modules/`、`.pnpm-store/` |
| `frontend/` 源码及包内 `static/live-console/` 构建资源 | `dist/`、`build/` 与测试、编译、覆盖率缓存 |
| `.gitignore`、`.editorconfig`、空的 `.env.example` | `.env` 与其他 `.env.*` 真实配置、IDE 和系统文件 |
| `.agents/` 中共享的开发技能、来源清单及配套许可 | 本地 `blobs/`、`codex/`、MinerU 锁、分词缓存、临时探针目录、根目录 `tmp????????` 探针和 `[--json]` 空产物 |

`.agents/` 是开发辅助资源，不是应用运行依赖；本项目保留其共享方式与第三方许可。
项目根目录目前没有项目自身的 LICENSE；公开开源前应由维护者选择许可，第三方技能许可不能替代项目许可。

准备首次提交前，在根目录预览：

```powershell
git status --short --untracked-files=all
git add --dry-run .
git check-ignore -v .env data/ .venv/ frontend/node_modules/ .idea/
```

`.gitignore` 只影响未跟踪文件；后续若有敏感文件已经入库，新增忽略规则不会从历史中移除它。
Git 克隆只包含代码和共享配置；数据库、凭证及运行资料需按业务约束另行迁移。

## 离线开发检查

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/check_architecture.py
uv run pytest -q
```

测试结果不等于真实来源接入、模型生成、OCR 或 GPU 推理验证；真实调用按对应任务单独授权。
