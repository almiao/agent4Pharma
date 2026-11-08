# MinerU 文档解析接入

已安装全局 `mineru` 技能；CLI 使用用户级独立 Python 3.12 环境，不加入项目 Python 依赖。当前实测版本 4.1.3。Windows 环境 `uv tool install` 因 EXDEV 原子重命名失败，改用 `.codex/tools/mineru` 独立环境和 pip 安装，未修改系统 Python 包。

## DSH 入口

正式制作角色保持关闭通用 Shell 和任意文件读取。另提供绑定原文的 stdio MCP：

```powershell
uv run python scripts/configure_mineru_dsh.py --root data --download-id <已关联的下载账本ID>
```

该命令校验 PDF 账本，生成 `data/agent/mineru-<ID>.patch.json`。SDK 采用现有 `sdk-minimal` profile 并传入该 patch 即可使用；不会启动付费模型或执行解析。不要把此 patch 与制作角色的另一套 `pharma` MCP 配置混用。

MCP 实际启动命令为 `python -m agent4pharma.entrypoints.mcp.mineru_document_server --root <data绝对目录> --download-id <ID>`。唯一工具 `read_document(start_page=1, end_page=1, after="")` 每次读取至多连续三页，截断时可原样传入返回的 after 游标继续；不接收文件路径、网址、Shell、质量降级、远程上传或服务配置参数。每次读取重新检查账本、项目资料目录、PDF 文件头和哈希；解析返回的内容哈希必须一致。返回保留 MinerU 质量级别、截断标志、页码/段落定位和原始续读信息。

当前入口只接已关联 PDF；全局 CLI 自身支持图片、Office 等格式。现有论文判断、写作和审核已接入按需阅读；旧任务、旧稿和旧证据快照保持原样。

## 运行台工作目录（2026-11-04，ADR-086）

应用统一采用 `<data根目录>/services/mineru`；本机实际路径为
`D:\pycharm\pythonProject\agent4Pharma\data\services\mineru`。
`mineru_runtime.py` 生成子进程专用环境，composition 把同一路径注入健康检查、官方服务控制、
解析队列、文档准备、正文读取、身份查询与图目录/图片导出，避免只修好状态查询而制作仍访问旧库。
父进程/系统环境和用户原配置不改写。页面“服务与任务 → 依赖服务 → MinerU → 工作目录”可查看实际路径。

- `doclib.db`、`doclib/`、`logs/`、端点及官方锁均在此应用目录。
- `models/` 是独立可写目录，模型仓库分别链接到 `D:\AI-Data\MinerU\models` 的既有权重；
  `.locks` 与 `.derived` 留在应用目录。不把整个 models 目录链接到无写权限的旧模型根目录。
- 通过 `MINERU_CONFIG` 只读复用既有用户配置（显式配置优先）；数据库、缓存、日志、端点和模型根目录
  由本次子进程环境固定覆盖。模型源为 local，并开启 Hugging Face/Transformers 离线模式，不自动下载。
- 直接在终端执行 MinerU 不会自动获得运行台的子进程环境；诊断需采用同一环境生成函数，
  不要将默认 C 盘 CLI 状态误当成运行台独立服务状态。

本机旧 `C:\Users\Administrator\.mineru` 的 ACL 看似允许写入，但锁的写打开和新建文件均实际拒绝；
不是已确认的单个遗留锁占用。没有删除旧锁、修改 ACL 或关闭系统防护。
在无运行服务、无待执行解析/扫描/监视任务时复制原数据库及缓存，校验数据库 SHA256 一致，原目录保留。
迁移清单位于 `data/services/mineru/migration-source.json`。模型旧根目录同样不能写运行锁，
故仅链接只读权重仓库，应用目录承担运行锁与派生缓存。

显式启动官方服务后健康检查通过；3份文档、11条历史解析记录保留，缓存正文读取可用且哈希身份匹配，
原数据库哈希未变化。本次只读取旧缓存，没有提交新解析、调用采编模型或重跑业务。
状态区分未安装、未启动、启动中、权限异常、检查失败、不可用与查询超时；超时保持暂未确认。
权限异常不展示启动/重启按钮，服务端也拒绝用启动绕过访问失败；健康的 MinerU 仅显示重启。

## 本地运行

模型服务与 CLI 安装分开验证。`mineru version --json` 验证版本；`mineru server status --json` 检查本地解析引擎。未配置模型时只能报告真实不可用状态，不能声称 OCR 已可用。模型档位、模型下载和服务配置按本次人工选择执行。

适配器先按 PDF SHA 和页窗执行本地 `read --json`，缺少缓存时才执行 `parse --json`，每次软输出限制 12,000 字符、等待 60 秒、进程硬时限 90 秒，并禁用本次进程的遥测。不提供自动上传、自动降级或其他解析器回退。依 error.code 区分配置错误、排队、超时和失败。配置为远程解析的规则不属于此入口的使用范围；部署应保持本地引擎。

验证包括离线协议/哈希/范围测试、真实 CLI 版本和实际 stdio MCP 工具发现。完整 OCR 需本地模型服务就绪后对真实文件验收，不以替身结果冒充。

## 本机验收（2026-11-01）

人工选择本地 standard / CPU，使用 ONNX 小模型和 llama.cpp；两组模型通过 `models verify`，托管服务 healthy=true。启动配置通过 MinerU 的 `update_config_file` 写入 model.small_backend=onnx、model.onnx_device=cpu、model.vlm.engine=llama-cpp（这些不是 `mineru config set` 支持的 Doclib 运行时键）。

真实已关联 DynaVelo PDF 第一页经 stdio MCP 返回 standard 正文 4,589 字符、文件哈希匹配，首次约 7.5 秒。已保留原生 content_ranges 起止定位及 next_request；重复读取通过文件 SHA 绑定的 `read .../tier:standard/page:...` 命中缓存，两次 MCP 验收为 2.6 / 2.4 秒；缓存缺失才执行 parse，避免重复等待入库解析。结果保存在 data/reports/mineru-mcp-acceptance-20261101.json。

本次仅验证一页真实 PDF 与协议、范围、哈希、缓存和质量门槛；未宣称所有图片/Office 或论文表格公式均已验收。

## 当前论文流程：目录优先、按需取证

模型初始上下文不能包含整篇论文，不能通过候选 evidence、历史稿件 evidence 或全部引文嵌套绕过。宿主不预取整篇 MinerU 文本；已有分页缓存可以复用，但未被请求的页不因进入流程而解析。人工导入仅检查文件结构、哈希与首页身份，首屏核验文本标为 identity_first_page，不再生成整篇纯文本旁路。

- 论文判断：get_intelligence_task 只给准则、题录、已知摘要和证据目录；get_primary_document 返回绑定身份及页数；read_primary_document 按需取最多三页，只有实际返回的片段才登记为 primary_fulltext_excerpt。最多20个独立读取请求，与摘要/网页补查预算分开。目录或已关联 PDF 本身不能冒充已读证据。
- 制作角色：get_production_context 只给任务书、稿件和证据目录，不包含 candidates.evidence、draft.evidence 或整套引文原文。read_document 默认一页、最多三页；read_evidence 每次最多6,000字符；find_bound_evidence 返回有限连续片段。读取与检索共享该角色冻结的读取预算。网络/解析在短事务之外，返回后再次检查租约和选题版本。
- 引文：可提交 source_span={content_hash,start,end}，程序从已登记证据提取连续原文，再保存实际 quote。过期 hash、越界位置、冲突 quote、文档目录引用均拒绝；不做模糊替换或放宽语义。编辑仍需独立核对引用是否支撑正文。
- 已读范围用 read_scope、truncated、next_request 和 MinerU 定位保存。片段状态为 excerpt，不显示为读完全文；不把未读章节猜作已核实。

本篇真实续跑中，写作按需读取第1页与第2—3页，审核读取第2—3页并复核第3页；没有向 DSH 返回整篇23页论文。第三版稿件7条引用全部使用位置绑定，通过独立审核，已生成并本地导出 Markdown 渠道包。对应实际工具记录、稿件与审核结果保留在原选题内。
