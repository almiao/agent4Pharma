# 来源字段与本地 Decider 预选

## 当前本地服务与驻留方式（2026-11-04）

当前配置及新环境默认端点为 `http://127.0.0.1:18895`；历史记录中的8895不回写。
本机8895没有监听服务，但存在其他进程持有的TCP绑定/已建立连接，实际bind返回WinError10013。
原启动脚本先加载模型、打印ready，再绑定端口，导致界面先显示启动中，随后进程退出。
此次改用已经验证可绑定的18895，不终止占用旧端口的外部进程，不修改系统端口策略。

控制器与独立脚本均在加载前检查端口，失败明确报告端口访问/占用问题，未加载模型；
ready只在HTTPServer绑定成功后输出。界面启动期间显示当前操作进度，避免同时展示旧健康超时文本。
端口检查与最终绑定之间仍有操作系统竞争窗口，最终绑定失败仍会如实退出，不冒充启动成功。
健康成功仍要求真实接口返回指定模型身份，不以进程存在、端口连通或ready日志代替。

建议手动按需启动，在预选及排序批次期间保持模型驻留并复用；不设开机自启，页面GET不加载模型。
当前实现启动后持续运行，关闭网页不会停止，也没有空闲自动卸载；工作台关闭后服务可继续存在，
新工作台不接管外部进程重启。若后续需要空闲自动释放，应另设明确的生命周期与进程所有权方案，
不能因健康超时自动杀进程或逐篇重载模型。

本机RTX4080启动及健康检查约19秒，整卡显存使用由978MiB升至9984MiB；此差值仅作本次驻留成本参考，
不是推理峰值或独占显存承诺。真实/health已确认模型、revision和decide/rank_logits能力身份。
本次没有调用/decide或/rank，也未重新预选、排序或制作。
503项Python测试、87项前端测试、Ruff/格式、架构检查及前端构建通过；运行台已重载并在Chrome显示Decider正常。


## 2026-11-04 阶段提示词收敛

资料预选须同时具备共用主题范围内的具体对象、实质 AI 关联，以及对象之外至少一项可继续分析的具体信息。
方法与问题、模型能力、实验或评测发现、资源用途、产品进展及明确产业事件均可构成线索；泛化宣传不够。
不比较创新程度、成果质量或最终新闻价值，不加入目标读者门槛；标题满足条件时不因缺摘要排除。
阶段模板见 `modules/model_configuration/screening_prompts.py` 的 `TRIAGE_STAGE`，仅用固定 choice 二选一，
不输出解释。运行台通过新草稿发布后供新任务冻结；既有发布版本不会因模板编辑自动替换，历史运行保持原快照。
下文 `ai-biology-binary-v4` 及旧提示词原文为独立随包策略与历史运行说明，不能代替当前发布版本。

本次已通过既有草稿/发布契约发布运行台资料预选 v5（`prompt-dd7fa1aa684f4c119f362b9012520604`），
保留原共用主题标准、精筛版本及历史预选正文；未执行真实模型或重跑资料。

更新：用户已明确本地预选只有“进入详情分析／不进入”两项。新准则为 `ai-biology-binary-v4`，删除第三项与 0.95 排除阈值，具体实际指令见[提示词原文](rss-triage-prompt.md)。下文 167/55/1,277 是旧三分类试运行记录，只保留历史，不代表当前流程。技术失败保留重试状态，不是第三个业务判别结果。

已实现并真实运行：RSS 发现 → 逐源字段提取 → 内容/版本去重 → 本地 Decider 二选一 → 进入情报原文复判队列或不进入。通过后继续正式候选与主编流程；历史批次没有重写。

## 全来源统一预选（2026-10-31）

- 用户要求关键词只用于模型提示/检索式，不能作为已采集资料的固定过滤条件；因此新批次不再有“关键词不命中即排除”的分支。`assess_coarse` 只负责把新资料放进 `awaiting_triage`，由本地 Decider 统一分类。
- 非 RSS 来源（PubMed/arXiv/bioRxiv/medRxiv API、配置化媒体）没有 `rss_signal` 时，用标题、原生摘要（作为 `abstract` 字段）、期刊与文章类型作为判别输入；原生摘要缺失则只有标题。判别输入仍按内容、模型修订与完整准则 hash 去重。
- `triage.enabled` 未开启时没有可用判别器：资料全部直送情报队列，报告标识 direct，不退回关键词过滤，也不冒充已分类。
- `analyze` 的全文门槛按输入质量决定：只有标题或 Feed 简介时保留 `require_primary_read`，推荐前必须取得可确认全文；原生摘要作为直接证据，不重复抓取。`exclude` 记 `not_selected`。
- 关键词表保留在检索式编译（`render_queries`）、工作台预览和历史核对中，不参与资料去留。已发布筛选策略的文字需显式再发布后更新，历史快照不改写。
- 本轮未运行真实 Decider、远程情报模型或发行；真实比例需以启用后的新批次为准，不能把历史 135/1,364 当作新准则结果。

## 当前二分类全量回归

2026-10-30，本地 Decider 按新版提示词重新推理全部 1,499 篇，批次 `daily-2026-10-29-7ececbae9111168c`：135 篇进入详情分析，1,364 篇不进入；待判别 0，无第三类。69 个来源采集覆盖 complete。未调用远程情报模型或执行发行。

针对已发现问题核对实际结果：`Cd47 deficiency in mouse brain under excitotoxic stress` 不进入；粪菌移植临床队列不进入；CellPatch 单细胞基础模型与 LoRA-N-Gly 蛋白语言模型均进入。它们是定向回归样本，不能据此推导总体准确率或漏召率。

管理台展示本批次全部输入、概率、路由和冻结的二分类提示词；API 快照与源码准则完全一致。运行产物为 `data/reports/rss-triage/summary.json`、`decisions.json`、`decisions.csv`。旧三分类产物另存 `summary-ternary-v2.json`、`decisions-ternary-v2.json`。验证：全量离线 149 测试通过；最终小改后相关 9 测试、Ruff、格式检查通过，架构检查 0 错误、12 个审查提示。

## 历史三分类实测与质量判断（已被上述二分类替代）

2026-10-30 重新调查 90 刊：69 个 RSS 可用，21 个仍不可用。对成功来源逐字段检查并写入数据库计划，再联网复采，按正式日期窗口归档并逐篇判别 1,499 篇。最终批次 `daily-2026-10-29-c489a54e99d26b4c`，采集覆盖 complete，本地待判别 0。

| 程序路由 | 数量 | 后续 |
|---|---:|---|
| 进入详情分析 | 167 | awaiting_intelligence，待情报 Agent 获取原文复判 |
| 高概率范围外 | 55 | not_selected，保留判别历史 |
| 信息不足或排除概率不足 | 1,277 | needs_triage_evidence，独立待补证，不自动启动付费分析 |

模型原始选择：analyze 167、exclude 829、insufficient 503。只有 exclude 概率至少 0.95 才硬排除，其余 774 条倾向排除也暂存补证。0.95 是初始保守策略，**不是经过领域标注集校准的正确率**。

1,034 篇有实际 RSS 文本，465 篇只有标题等线索。10 个源的候选简介字段本轮只有书目信息：Advanced Drug Delivery Reviews、Biotechnology Advances、Current Opinion in Structural Biology、Drug Discovery Today、Journal of Molecular Biology、Journal of Pharmaceutical Analysis、NEJM Evidence、Science、Science Advances、Science Translational Medicine。可以凭标题判别，但不能宣称有摘要。

第一轮有 184 条 analyze；随后发现 Elsevier 作者/日期串误当内容，修复字段质量，补充“计算模型不自动等于 AI、transformer base editor 不自动等于神经网络”的准则，分离补证队列，再完整模型重跑。第一轮文件保留 `summary-v1.json`、`decisions-v1.json`。第二轮 `b72497a126fcf108` 和最终冻结配置批次均保留；最终 1,499 个成功判别 ID 与第二轮一致，复用了结果，没有再次推理未变化文章。

**链路达到本次功能目标，质量仍是试运行，不能认定稳定生产可用。**明确的蛋白语言模型、单细胞基础模型、医学深度学习、分子强化学习能够选入，但 85.2% 待补证，效率仍有明显不足。

助手按三路各取判别 ID 排序前 8 条，检查了 24 条真实 RSS 标题与文本：8 条 analyze 中，6 条有明确领域线索，一条粪菌移植临床队列未体现 AI，另一条 RPCA 多组学方法需核实 AI/ML 关联；8 条硬排除均为材料/能源/物理内容。待补证中还有经济学诺奖评论等可能离题内容，不能把它们都称为潜在相关论文。

这是非盲、非专家、非代表性的助手抽查，不能推导整体 precision/recall 或零漏召。逐项输入、概率、评阅理由在 `data/reports/rss-triage/quality-review.json`，不修改正式结果。离线降低排除阈值至 0.90/0.85/0.80 会分别排除 102/187/242 条，本轮未据此修改阈值。后续需要人工标注集、摘要补证和补证预算，不冒充已完成。

## 来源字段与架构

`CollectionPlan.rss_profile` 保存每刊实际 `text_fields`、分类字段、刊名、每字段存在数量/总数/长度中位数/角色、核验时间和原件 SHA-256。提取器版本为 rss-signal-v2。原始 RSS、清理后信号和模型结果分别保存。

输入为标题、真实语义字段、分类、可得文章类型及刊名。DOI、链接、作者、原始日期保留作身份/出处。HTML/脚本清理，重复和包含文本去重；单字段 12,000 字符上限，截断显式标记。纯卷期页码、Elsevier 作者/日期串不是摘要；导读不等同完整摘要。无内容不生成补全文字。

缓存身份包含资料身份、实际输入、提取版本、完整准则/选项/阈值和模型修订。内容或版本变更重判，成功跨批次复用，失败不缓存为成功，同名不同论文不因标题相同而合并。默认新字段省略，旧计划序列化保持兼容。

SQLite v9 追加发现模块的判别历史、批次关联和错误表，升级前在线备份。资料通过资料仓储公开快照读取，网络/GPU 在事务外，判别与路由原子提交，没有扩展集中存储门面。

情报请求 `require_primary_read=true` 必须实际调用原文工具。只有保守检查确认研究正文的 HTML 才标 primary_fulltext；付费预览、未解析 PDF、失败或范围不明只能待补证，不能凭 RSS 直接推荐。这不是通用全文下载器，观点文和其他模板可能仍需人工补证。

## 模型部署

采用 [Perplexity 官方模型](https://huggingface.co/perplexity-ai/pplx-decider-flash-v1.2)，固定修订 `9ff7cf9838e59df461c276b7699d56b244d2af58`，官方专用非因果全注意力和 per-marker readout；不以普通聊天推理代替。

- 模型及官方源码：`data/models/decider-flash-v1.2/`，发布清单逐文件 SHA-256 核验。
- 独立环境：`data/runtimes/decider/`，pplx-decider 0.3.0、torch 2.14.0+cu132、torchvision 0.29.0+cu132、transformers 5.17.0 等；主项目不引入 torch。
- RTX 4080 16 GB 实际 CUDA 推理，官方 reference 路径 fast=False，温度 1.5539092221462472；未安装未验证的优化内核，因此较慢。
- 服务仅 `127.0.0.1:8895`，`/health` 返回真实身份；有界文本 JSON，不接受图片路径或跨站请求。项目本机调用 trust_env=False，不走系统代理。
- 本轮 Hub gated=false，可匿名取得文件，没有读取 Hugging Face 凭证；模型卡 private 是旧描述。

安装步骤（模型、环境和缓存均在项目 D 盘忽略目录）：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'data/runtime-cache/uv'
uv run python scripts/install_decider.py
uv venv data/runtimes/decider
uv pip install --python data/runtimes/decider/Scripts/python.exe -r data/models/decider-flash-v1.2/requirements.txt
uv pip install --python data/runtimes/decider/Scripts/python.exe torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu132
uv pip install --python data/runtimes/decider/Scripts/python.exe ./data/models/decider-flash-v1.2/source --no-deps
.\scripts\start_local_decider.ps1
```

启动器不创建第二个占用 GPU 的服务。没有注册开机自启，不宣称重启后仍自动运行。

## 操作与产物

```powershell
uv run python scripts/audit_journal_rss.py --output data/reports/rss-triage/audit
uv run python scripts/configure_rss_signals.py
uv run python scripts/run_rss_triage.py
```

分别联网调查、保存字段规则、正式复采和预选。配置 `[triage]` 已启用，日常调度共用该阶段；collect-only 包含本地预选，但不调用远程情报。本轮未执行远程精筛、主编或发行。

管理台 `http://127.0.0.1:8765/intelligence` 的“资料与本地预选”：节点和文章均可查看实际输入、字段出处、概率、程序路由理由、输入 hash、模型/准则版本，支持筛选。来源页有“查看 RSS 字段与提取规则”。Decider 没有生成自然语言理由，程序说明不冒充模型推理。原 8876 是旧静态原型。

产物在 `data/reports/rss-triage/`：`field-profiles.json`、`summary.json`、`batch.json`、`decisions.json`、`decisions.csv`、`quality-review.json`；原始 XML 在 audit 子目录。

147 项离线测试通过；Ruff、格式通过；架构 0 错误、12 条审查提示。覆盖预选持久化、版本去重、失败恢复、身份/概率校验、旧库升级及备份、信息质量和原文读取约束。真实模型/网络结果不以离线替身冒充。HTTP 管理页与带令牌 API 实测 200，无令牌 403；JS 语法检查通过，未完成浏览器视觉验收。composition 的长文件提示保留：本轮仅装配具体依赖和只读投影，业务及 SQL 已分离到专用文件。
