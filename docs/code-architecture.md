# 长期代码组织与编码规范

日期：2026-10-29（Asia/Shanghai）。本规范面向业务与团队持续增长，不以当前文件数量或行数作为架构目标。业务决策仍以 architecture-decisions.md 为准；实际文件映射见 [当前结构说明](refactor-layout.md)。

## 1. 业界实践与项目选择

| 实践及出处 | 本项目采用的规则 | 不应推出的结论 |
|---|---|---|
| [Bounded Context，Martin Fowler](https://martinfowler.com/bliki/BoundedContext.html)：大模型需要明确的业务边界和关系 | 以来源目录、资料、采集、发现、选题、内容、审核、发行划分职责；每个模块有自己的语言、数据与接口 | 不等于每个领域必须部署一个服务 |
| [Monolith First，Martin Fowler](https://martinfowler.com/bliki/MonolithFirst.html)：独立服务有额外成本，边界需要演化 | 先做有边界的单体，需要资源隔离时可拆 worker；独立扩容/发布需要出现后再拆服务 | 预计代码多不意味着立即微服务化；这也不是对所有项目的普遍证明 |
| [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)：模块与公开接口需要说明，相关类型可同处一个文件 | 模块说明职责，公开 API 说明语义；按相关职责组织文件 | 不要求一个类一个文件，不要求所有模块套同一层级模板 |
| [PEP 8](https://peps.python.org/pep-0008/#naming-conventions)、[PEP 257](https://peps.python.org/pep-0257/)：命名一致、文档字符串表达契约 | 英文 snake_case 文件/函数，PascalCase 类型；中文解释业务与约束 | PEP 没有规定每个文件只能 400/600 行，也没有统一的业务目录名称 |

下面的目录、文件角色和行数提示是我们的工程约定，不冒充统一行业标准。采用它们的理由是让新成员能定位变化、理解依赖、独立测试模块，并减少跨模块连锁修改。

## 2. 业务优先，层次服务于边界

```text
agent4pharma/
  __init__.py                    # 包说明，无资源初始化
  __main__.py                    # python -m agent4pharma
  composition.py                 # 具体依赖装配及所有权
  modules/                       # 按业务能力组织
    sources/                     # 来源、机构、载体及访问入口目录
    materials/                   # 资料身份、来源记录及采集数据契约
    collection/                  # 来源分页、恢复和覆盖
    discovery/                   # 候选、入选、简报校验及批次报告
  workflows/                     # 跨模块流程：每日发现、候选简报
  agents/                        # 角色定义、提示词、工具清单
  entrypoints/                   # CLI / MCP 协议适配
  runtime/                       # 配置、调度、锁和执行契约
  infrastructure/                # HTTP/来源、DSH、SQLite 的具体实现
  shared/                        # 无业务归属的少量稳定能力
```

未来增加 editorial/content/review/publishing 时，在 modules 下建立真实业务模块，不在整个项目横向追加一套 models、services、tools 总目录。小模块允许只有两个文件；同一业务模块确实复杂后，再在内部形成 domain/application 等子包。

### 每个业务模块的责任卡

模块 README 必须说明：业务含义、拥有的数据或规则、公开入口、允许的依赖、禁止承担的职责，以及一个新增需求应放在哪里。新增模块先写这张责任卡，再创建代码；不要先生成空目录模板。

| 模块 | 拥有的概念/规则 | 明确边界 |
|---|---|---|
| sources | 机构、账号、载体、期刊别名、指标、来源与入口目录 | 目录已录入不表示已经实现采集；不按 Agent 复制目录 |
| materials | 资料身份、来源版本、原始产物与出处契约 | 不决定资料是否值得选题；发布者与报道对象分开 |
| collection | 来源窗口、分页、检查点、重试与覆盖 | 不生成模型结论，不把失败当作零结果 |
| discovery | 批次成员、抽样/筛选策略、入选简报和访问范围 | 不决定正式立项，不写最终稿，不发布 |
| workflows | 多模块调用顺序、失败隔离和完成条件 | 不重写来源解析、SQL、身份或筛选规则 |
| agents | 角色说明、提示词和工具使用范围 | 不拥有跨角色资料库；提示词不能代替程序权限 |

## 3. 文件名称说明业务与职责

优先使用“业务概念 + 实现职责”，但已有上下文充分时不机械重复目录名。例如 connectors/pubmed.py 已能说明适配的来源；跨模块使用的 DailyLiteratureDiscovery 则要显式表达业务。

| 名称模式 | 应放内容 | 本项目实例 |
|---|---|---|
| `<business>_service.py` | 有校验、不变量或用例协调的业务服务 | discovery/batch_service.py |
| `<business>_repository.py` | 持久化契约或持久化实现，由所属目录区分 | discovery/batch_repository.py、sqlite/material_repository.py |
| `<business>_interfaces.py` | 外部能力契约，名称说明消费场景 | collection/acquisition_interfaces.py |
| `<business>_records.py` | 具名输入输出及值对象 | materials/collection_records.py |
| `<business>_identity.py` | 无 I/O 的身份规则 | materials/material_identity.py |
| `<business>_reports.py` | 数据投影的序列化/展示 | discovery/batch_reports.py |
| `<business>_commands.py` | CLI 参数到应用调用及输出 | cli/source_directory_commands.py |
| 明确的流程名称 | 跨模块业务编排 | workflows/daily_literature_discovery.py |

service.py、ports.py、bootstrap.py 本身并非“不符合行业标准”，但在本项目多模块并存时缺少业务限定，容易增加搜索与阅读成本，因此选择更明确的名字。

禁止新建无法说明所有权的 utils/common/helpers/base/manager；禁止 part1/part2、misc、新旧两套同名实现和永久转发文件。接口只调用另一接口一次、没有独立规则或协议转换时，先考虑删除这一层。

## 4. 依赖、公开接口与数据所有权

- entrypoints 负责协议适配；workflows 负责业务流程；modules 负责业务规则；infrastructure 实现模块契约；具体实现只在 composition 装配。
- 跨模块调用公开契约/服务，不访问对方私有函数或表。禁止循环依赖，不能用局部 import 或 sys.path 修补掩盖循环。
- 领域规则不依赖 HTTP、SQLite、DSH 或 CLI。外部 SDK 类型不能泄漏为业务接口；模型运行器可以被离线测试替换。
- 每张表有业务所有者。跨表原子操作通过一个工作单元协调，共用连接；内部写帮助方法不得各自 commit。网络、模型调用和等待在事务外。
- 当前 SqliteApplicationStorage 聚合发现试点的既有持久化操作，是单机应用适配器，不是领域实体。它仍是后续需限制增长的集中点：新增业务不得继续往其中堆全部方法，而应向对应模块注入专用 repository/服务。
- shared 不能因为“两个模块都会用”就接收业务概念；共享业务能力应有真实所有者。只有稳定、无业务归属的代码才进入 shared。

## 5. 什么时候应该拆分

以下情况要求评审职责，而不必等到行数增加：

1. 同一文件因两类互不相关需求经常一起修改，或类/方法无法用一句具体业务描述解释。
2. 一次业务变化必须穿过很多模块修改内部字段，或两个模块双向依赖。
3. 模块为了完成一项职责必须读取另一模块的内部表、状态或私有 helper。
4. 测试一个纯规则却需要启动 HTTP、数据库、模型或整个应用。
5. 网络协议、业务校验、SQL 和展示混在一个操作里，无法分别说明失败与事务边界。

评审时先确定概念和所有者，再按用例、协议、持久化或输出格式拆分；共享事务不能因拆文件被拆散。逻辑紧密相关的类型可以同文件，不按每个类/函数机械分文件，不为预想中的变化引入插件系统或空转接口。

行数只是自动提示：src 文件超过 400 行、函数超过 60 行要求检查内聚性；即使超过 600 行，也不凭行数单独判失败。保留较长文件需要解释其单一职责；迁移声明、解析映射等声明式代码可合理较长。禁止压行、删注释或无意义搬运来规避提示。真正阻断的是违反依赖边界、缺失接口说明和行为验证失败。

## 6. 注释与接口说明

所有源码模块写模块 docstring：说明提供什么能力，必要时指出边界和副作用，不能只写“xxx package”。公开类说明实例代表什么及资源/状态所有权。

公开函数和方法写 docstring，简单操作可一句话；复杂操作按 Google 风格使用 Args / Returns / Raises，按需要描述：

- 参数的业务单位、日期/时区/区间、标识符和访问范围；类型注解不用重复翻译。
- 结果的状态含义，区分失败、合法空结果、部分完成和未获取。
- 数据、文件、模型调用或费用等副作用；资源由谁关闭。
- 幂等键、事务参与/提交方式、重试条件及并发约束。

行内注释解释“为什么”和不能破坏的不变量，例如为何资料与游标必须同时提交、为何某种 DOI 只是关联出版物。不得逐句翻译代码；实现变化时同步修改说明，过时注释视为缺陷。

Ruff D100/D101/D102/D103 检查模块、公开类、方法、函数的说明是否缺失；测试和维护脚本豁免机械文档要求。工具只能证明有说明，是否准确、足够仍需评审。模块 README、docstring 和 ADR 分别解释模块导航、接口契约、跨模块取舍，不复制同一段说明到三处。

## 7. Python、数据和运行规则

- Python 3.12+、src 布局、uv。英文标识符、中文业务说明；公开函数提供参数与返回类型，结构化新业务结果使用 dataclass/TypedDict/Pydantic。已迁移历史 JSON 的 dict 注解暂保留，不扩散到新业务。
- Ruff 110 字符，E/F/I 与上述文档检查；统一 Ruff format。import 不联网、不打开数据库、不初始化 SDK、不读取密钥。
- 配置在启动边界加载校验；时钟、存储、连接器与模型运行器通过参数注入。可选 SDK 在实际使用时加载，并显式释放资源。
- 已知错误使用明确类型；仅在入口或任务隔离边界兜底捕获 Exception。禁止吞错、失败返回空列表或成功；持久化错误与日志必须脱敏。
- 网络请求指定超时、限流和有限重试；避免多层独立重试放大。stdout 服务于 CLI/MCP 协议，诊断走 stderr/日志。
- SQL 位于持久化实现；值参数化、INSERT 写列名、动态标识符来自程序白名单。schema 只前进，升级前备份并验证旧库/回滚；资料、成员与检查点原子保存。
- 时间点带时区，持久化 UTC；业务日期按 Asia/Shanghai 解释。上游缺时区/精度保留未知，缺失指标用 None，不填零。
- .env、密钥、data/ 数据和产物不入库，不读取或输出真实凭证。提示词、策略、模型和资料均保留身份/版本；审核与未来发行绑定具体稿件版本。

## 8. 新需求放置示例与验收

| 变化 | 放置位置与验收 |
|---|---|
| 新增 RSS/网站来源 | infrastructure/connectors 中增加适配，复用 collection 用例；测试分页/空结果/失败/恢复，不复制一套工作流 |
| 新增相关性筛选规则 | discovery 的具名策略及契约，验证候选状态；不改来源解析器 |
| 新增选题任务书 | modules/editorial 的业务模型和用例；与 discovery 用公开候选契约对接 |
| 更换模型 SDK | infrastructure/llm 适配，workflows 使用相同运行契约；离线替身测试不应依赖 SDK |
| 新增展示或协议 | 对应 reports 或 entrypoints；复用业务校验，不能重写另一套选中/权限判断 |

每次交付运行：

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/check_architecture.py
uv run pytest -q
```

规则用单元测试，SQL/迁移/事务用临时数据库测试，连接器用脱敏响应契约，少量端到端测试验证完整流程。默认离线、无付费调用；真实来源/LLM 冒烟需显式授权。结构脚本检测静态 import 环、分层依赖、跨模块访问、已删除旧入口引用以及持久化层外 SQL/db，行数只提示；不覆盖动态导入、逐表所有权或完整业务语义。类型检查器与 CI 尚未接入。
