# 当前代码位置与职责

## 2026-11-04 旧情报执行链退役（ADR-083）

旧intelligence_service/decision/runner/server、screen_discovery_batch、review_selected_papers及单篇回归执行已删除。
下载请求构造归workflows/selected_paper_download；intelligence_repository和旧回归仓储只读历史。
设置不再注册精筛节点；当前筛选由select_local_papers和compare_paper_news执行。
本节覆盖下文旧角色与文件映射，旧数据和引用保持可读。

更新：2026-10-29。本文替代上一轮以文件行数为中心的重构记录；长期约定见 [代码结构与编码规则](code-architecture.md)。

## 阅读顺序

1. composition.py：应用装配，创建实际存储、来源连接器与模型运行器。
2. workflows/daily_literature_discovery.py：一次每日发现的整体调用顺序。
3. modules/collection/source_collection.py：单来源分页、失败隔离与续跑。
4. modules/discovery/batch_service.py：批次访问范围、入选与简报引用校验。
5. infrastructure/persistence/sqlite/：具体查询、写入和跨表事务。

每个业务包及 workflows 中的 README 提供责任卡。模块、公开类和函数的 docstring 说明能力、输入输出及约束；不要靠文件长度推断边界是否合理。

## 已固定名称

| 原位置/名称 | 当前位置/名称 | 职责 |
|---|---|---|
| bootstrap.py | composition.py | 组合具体依赖；不是业务服务 |
| workflows/daily_discovery.py / Workflow | workflows/daily_literature_discovery.py / DailyLiteratureDiscovery | 每日文献发现编排 |
| 旧独立候选简报 | 已移除（2026-11-01） | 精筛摘要、推荐理由和证据直接交主编 |
| workflows/ports.py | workflows/discovery_dependencies.py | 两种流程需要的持久化能力组合 |
| runtime/config.py / Config | runtime/settings.py / ApplicationSettings | 启动配置、策略快照和日期映射 |
| runtime/ports.py | runtime/execution_interfaces.py | 资源关闭、任务记录、调度、模型执行契约 |
| collection/ports.py | collection/acquisition_interfaces.py | 来源分页、请求额度和采集存储接口 |
| collection/service.py | collection/source_collection.py | 单来源采集用例 |
| discovery/service.py / LiteratureTools | discovery/batch_service.py / DiscoveryBatchService | 共享批次业务校验；MCP 只是调用者 |
| discovery/ports.py | discovery/batch_repository.py | 批次访问的持久化契约 |
| materials/contracts.py | materials/collection_records.py | CollectedRecord、CollectionPage、SourceResponse、PersistedRecord |
| materials/identity.py | materials/material_identity.py | 资料身份规范化 |
| sources/identity.py | sources/venue_identity.py | 载体身份规范化 |
| sources/ports.py | sources/directory_repository.py | 来源目录导入与查询契约 |
| sources/service.py | 已删除 | 原来只有转发，无独立规则；调用者直接使用目录契约 |
| sqlite/store.py / Store | sqlite/application_storage.py / SqliteApplicationStorage | 当前发现试点的连接及聚合持久化适配 |
| sqlite/materials.py、jobs.py 等 | material_repository.py、job_repository.py 等 | 文件名标明其持久化职责 |
| agents/intelligence/definition.py | agents/intelligence/role_definition.py | 工具清单与包内提示词加载 |
| cli/parser.py、directory.py、execution.py | argument_parser.py、source_directory_commands.py、discovery_commands.py | 语法、目录子命令、发现子命令 |

sqlite 还包含 source_access_repository、collection_page_repository、discovery_batch_repository、venue_repository、source_directory_repository 和 catalog_importer。migrations 保留 v001/v002/v003 及顺序执行入口；模块 docstring 解释各自事务参与方式。

## 删除重复入口及兼容范围

上一轮留下的根模块 agent.py、catalog.py、cli.py、config.py、connectors.py、models.py、registry.py、store.py、tools.py、transport.py、workflow.py 已移除，内部测试和脚本均改用实际实现。Python 包根只保留 __init__.py、__main__.py、composition.py 及有职责的子包。

稳定命令入口为 pharma 或 python -m agent4pharma；不再承诺未发布的旧内部 Python 导入路径。CLI 子命令、MCP Tool 名称/参数与输出、schema v3、现行抽样和默认模型保持原语义。提示词唯一源文件仍为 agents/intelligence/system_prompt.md，随 wheel 分发。

## 已做与尚未解决的事项

- 已完成命名和入口清理、公开接口/类/模块说明，并启用 Ruff 文档缺失检查。
- 资料与游标仍在同一页事务提交；目录 demo 整体导入仍原子；没有因拆文件改变事务边界。
- 行数改为维护提示，不能单独导致结构检查失败；旧记录中的“600 行硬失败”不再适用。
- SqliteApplicationStorage 仍聚合试点操作。名称清楚不等于解除了耦合：新业务必须通过各模块专用契约注入，不能继续扩张这个对象；后续出现独立用例和数据所有者时继续收窄。
- 结构脚本不证明动态调用或逐表所有权；类型检查器与 CI 尚未接入。docstring 的正确性和业务边界仍须评审。

本轮验证包括离线回归、Ruff/格式/静态结构检查和 wheel 资源检查。真实来源和模型调用不作为本轮验收。

## 来源维护工作台增量

2026-10-29 后续通用采集增量：sources/collection_plans 定义规则契约，sqlite/collection_plan_repository 拥有配置及版本，connectors/configured_web 和 web/configured_extraction 实现所有站点共享能力，cli/acquisition_commands 适配数据库计划管理与运行。schema v6 升级前备份，不扩大 SqliteApplicationStorage 门面；配置和四站真实复测见 [通用媒体采集](configured-media-collection.md)。

- `modules/maintenance/models.py`：样本、调查快照、页面证据和具名结果；`investigation_tools.py` 校验任务范围、页面预算和模型引用；`provenance_analysis.py` 处理真实引用链候选。
- `modules/discovery/screening_policy.py`：Agent/LLM＋制药词规则和策略版本契约；`modules/sources/management.py`：来源编辑公开契约。
- `workflows/maintenance_workbench.py`：排队与快照；`investigate_article.py`：规则/Agent 调查流程。`composition.py` 装配。
- `entrypoints/web/api.py`、`server.py`：本机协议；static 的 sample_form/source_form/policy_form/job_view 按业务交互拆分，core 仅 DOM/API 基础操作，app 负责装配。
- `entrypoints/mcp/investigation_server.py`：六个任务工具；`infrastructure/web/`：公开页面读取/解析；`infrastructure/llm/investigation_runner.py`：受限 DSH 适配。
- `infrastructure/persistence/sqlite/` 新增专用策略、来源、调查、证据及只读查重适配，`migrations/v004.py` 前进升级并备份。没有迁入旧聚合存储内部业务。

工作台是 schema v4 增量，不代表前文历史重构时的 schema v3 不变说明适用于新功能。当前使用边界见[运行台说明](workflow-management-console.md)。


## 两 Agent 与筛选切换

- `agents/source_maintenance/system_prompt.md` 与 `agents/intelligence/screening.md` 分别定义两个角色；旧 system_prompt.md 只作补简报。
- `modules/discovery/intelligence_contracts.py` / `intelligence_service.py` 统一正式、复测、自检和引用校验；`sampling.py` 已删除。
- `modules/maintenance/agent_tools.py` 提供目录核对、同版情报复测和草案；`change_contracts.py` 定义待审变更。
- `sqlite/intelligence_repository.py`、`intelligence_readings.py` 和 `batch_intelligence.py` 分别管理评估、补读和批次写回；`coarse_screening.py` 把新资料交本地预选，不再用关键词过滤。
- `sqlite/maintenance_changes.py` 协调人工确认工作单元，各参与仓储共享连接、不各自提交；`directory_inspection.py` 与 `venue_maintenance.py` 处理目录事实和期刊更新。
- `workflows/screen_discovery_batch.py` 有界续跑精筛；`entrypoints/mcp/intelligence_server.py` 暴露绑定情报工具；`entrypoints/web/static/review_views.js` 展示建议、维护反馈和主编待选。
- `infrastructure/connectors/rss_feed.py` 只采集已验证并确认启用的 RSS/Atom；`web/intelligence_reader.py` 只补读绑定 URL。数据迁移 v005 前备份，旧批次保留。

## 自动返修与证据交付

- `modules/content/draft_revision.py`复用准确补丁合成；`revision_workspace.py`拥有宽松候选、不可变保存版本及正式提交游标。
- `workflows/revision_workspace.py`提供候选读取、编辑、恢复和预检；`revision_workspace_state.py`校验冻结原稿与问题身份，避免交稿用例反向依赖工具装配。
- `draft_submission_validation.py`由候选预检和正式交稿共用；`production_story_state.py`集中准确选题状态守卫。
- `evidence_delivery.py`只管理当前MCP会话内公开原文窗口的覆盖，不承担研究、持久化或模型历史裁剪。
- 持久候选沿`ProductionTask`已有JSON载荷保存，`production_unit`协调原子更新，不新增独立数据库或业务专属追踪。


返修活动窗口：runtime/context_window_policy.py拥有冻结预算；infrastructure/llm/dsh_revision_window.cjs负责Harness可见历史替换与有界压缩，context_visibility.py提供可见代次读取，dsh_tool_readiness.cjs阻止工具未挂载时调用模型。runtime/production_lifecycle_cost.py汇总准确父子链全部尝试，scripts/inspect_production_lifecycle.py与HTTP只读入口共用；业务候选仍归content/workflows，不迁入SDK适配器。
