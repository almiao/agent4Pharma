# 仓库开发约定

日常开发先读[文档索引](docs/README.md)与[功能状态表](docs/console-migration-matrix.md)，再按任务阅读专题契约。[代码架构](docs/code-architecture.md)规定工程边界，[模块布局](docs/refactor-layout.md)帮助定位代码，[有效架构决策](docs/architecture-decisions.md)解释关键取舍。

本文件只维护当前开发约束，不追加按日期排列的实施过程、测试结果或临时故障记录。业务规则在对应专题文档维护；改变架构取舍时更新 ADR，删除或明确替代失效规则。

## 业务入口与执行边界

- 主题为 AI × Biology/Pharma，纳入非语言生物基础模型，排除仅使用传统机器学习的研究；详见[主题范围](docs/research-scope.md)。主题确认不等于上游召回已全面覆盖。
- 论文预选、新闻排序、商业整体比较、人工取得主文并推荐的边界见[筛选流程](docs/paper-selection-pipeline.md)及[本地排序](docs/local-rss-triage.md)。不恢复旧逐篇情报精筛、单篇回归执行或随机抽样。
- 当前采编从主编选题开始，经作者研究写作、阅读评审、事实核验、图文制作至人工定稿；角色和阶段标识见 ADR-115。操作与权限见[生产说明](docs/production-user-guide.md)，评审结论和共用返修额度见[阅读评审](docs/reader-editing.md)。
- 工作台启动、重做、继续和重试即请求模型执行；浏览、刷新、配置保存/发布不授权执行。启动只恢复显式持久化 auto_run 意图，不从推荐、idle 或历史排队状态推导新调用。
- 继续/重试复用准确任务的冻结输入、配置与已提交产物；重做使用当前配置另建根任务。模型会话未提交内容不算保存点，历史快照不得静默升级。
- 人工修改见[图文审稿](docs/article-editing.md)，人工批准、导出和公众号草稿箱交付见[图文制作](docs/publishing-layout.md)。草稿箱交付不等于公开发布；旧批准不能沿用于新包。
- 依赖服务显式启动，不隐式下载模型、删除锁或接管外部进程。数据清理需明确授权、备份及引用校验；有外部交付时须保护准确批准包和防重依据。

## 架构边界

- Python `src/agent4pharma` 布局、uv、模块化单体，按业务能力划分 modules；不用 Agent 名称划分数据孤岛。
- 目标目录为 entrypoints、modules、agents、workflows、runtime、infrastructure、shared、composition.py；有实际代码时再建包，不生成空模板。
- 业务模块拥有规则、用例、公开契约和数据。跨模块通过公开契约与服务访问，不读写对方内部表。
- CLI/MCP/HTTP 只适配协议，共用用例；SQL 归持久化实现，DSH SDK 归基础设施适配器。
- 依赖方向：入口/流程/角色 → 应用用例 → 领域与端口；基础设施实现端口；composition 装配具体依赖。禁止循环导入和 sys.path 修补。
- 一个跨模块原子操作由应用用例协调同一工作单元；repository 不各自 commit。网络、LLM、休眠不得放在数据库事务内。
- 角色定义不替代权限、范围、预算和版本校验。确定性任务状态与重试由程序执行，模型不能绕过业务校验。
- shared 只放少量稳定的无业务归属能力；禁止新增无边界 utils/common/helpers 和全能 Store/Manager。
- 沿用现有业务行为。根目录旧转发模块已经删除，内部调用只使用实际实现；命令入口为 pharma 或 python -m agent4pharma。新增业务不得重新引入重复兼容层。

## 可读性与模块导航

- 每个业务模块维护 README 责任卡：拥有的概念、公开入口、允许依赖、边界及新增需求放置示例。
- 文件名按业务和职责命名，如 batch_service.py、material_repository.py、daily_literature_discovery.py；不套一个类一个文件的模板。
- 模块 docstring 解释职责；复杂公开 API 说明参数语义、返回状态、异常、副作用、事务、幂等及资源所有权。注释解释原因和不变量，不逐句翻译代码。
- service.py/ports.py/bootstrap.py 并非行业禁用名，但本项目优先用带业务限定的名称。没有校验/转换/独立用例的转发层应删除。
- 当前 SqliteApplicationStorage 只服务既有试点，新增业务直接注入专用仓储/服务，不能继续扩大集中门面。

## 界面与交互

- 正式后台为 `frontend/` 中的 React + Ant Design 工作台，构建产物随 Python 包分发。旧地址仅转换准确对象及范围，不恢复旧页面、原型或失败回退。
- 列表、侧栏计数与下钻共用对象集合；关联使用真实身份，未知显示未知，不按标题或日期猜配。详情、返回和刷新保留 URL 中的集合、筛选与定位。
- 遵循[界面准则](docs/interface-design-principles.md)：以层级、控件、状态和操作反馈表达功能，说明按需就近提供，保留字段标签、恢复路径、操作后果及可访问性。
- 输入输出与过程查看复用[通用执行追踪](docs/execution-tracing.md)，不建立业务专属 trace、不补造历史关联，不把 SDK 可见上下文称为供应商 HTTP 报文。

## 编码

- Python 3.12+，公开函数参数/返回值有类型；新业务契约不用裸 dict/Any；已迁移的历史 JSON/Tool 返回结构与 dict 注解暂保留，新增字段或类型迁移单独验证兼容。外部边界用 Pydantic，内部对象按需用 dataclass/TypedDict。
- 英文标识符，snake_case 函数/模块，PascalCase 类；中文说明解释业务原因与约束。
- Ruff 110 字符，E/F/I 与 D100/D101/D102/D103；src/tests/scripts 统一 Ruff format。源码模块、公开类、公开函数/方法必须有准确说明，测试和维护脚本文档检查豁免。
- 文件 400 行、函数 60 行仅触发项目审查提示，不按行数单独判失败。首先评审内聚、耦合、变更原因、数据所有者和可测试性，再按职责拆分；禁止压行或无意义切片。
- import 不执行联网、SDK 初始化、密钥读取、数据库打开。可选 SDK 允许有理由的局部 import。
- 已知错误有明确类型；仅在入口/任务边界兜底捕获 Exception。禁止吞错、失败转空结果或成功；错误和日志必须脱敏。
- 网络有超时、有限重试和限流；配置在启动边界加载，具体资源通过参数注入并显式关闭。
- SQL 参数化，INSERT 写列名；迁移版本前进，保留旧库升级、备份和回滚验证。资料与游标原子提交，重跑幂等。
- 时间点带时区并持久化 UTC，业务日按 Asia/Shanghai；上游时间精度不足时保留未知状态。
- `.env`、密钥、data/ 运行产物不入库；不要读取或展示真实凭证。prompt、模型、资料和审核均记录版本/身份。

## 验证与交付

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/check_architecture.py
uv run pytest -q
```

- 默认测试离线、无付费调用。按变更验证规则、连接器契约、事务、迁移、幂等、失败恢复或权限范围；不为纯文档修改增加行为测试。
- 真实来源与 LLM 冒烟需显式任务授权；不得以 fixture 或运行时初始化冒充真实模型验证。
- 结构脚本检查静态 import 环、分层依赖、已删除旧入口引用、持久化层外 SQL/db 和文件增长；不覆盖动态导入或逐表所有权。类型检查器和 CI 尚未接入，不报告为已启用。
- 修改业务时同步相应文档；架构取舍更新 ADR，合并被覆盖的规则并保留编号去向。报告修改结果、实际验证和限制。
- 参考项目 TheVirtualBiotech 仅作结构参考；不直接迁入科研角色、Claude SDK 或整个目录模板。
