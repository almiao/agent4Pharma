# editorial 责任卡

- 拥有：编辑决定、选题任务书与变更。
- 公开入口：commission_contracts 的类型与 validate_decision；应用交接由 workflows 消费，不读取其他模块内部表。
- 依赖：标准库/Pydantic；不依赖 SDK、网络、CLI、Web 或持久化实现。
- 专用仓储位于 infrastructure/persistence/sqlite/production_repository.py，工作单元统一提交；不是集中存储门面扩展。
- 新需求放置示例：新增选题验收字段放 commission_contracts，跨阶段事务在 workflows/production_handoff 协调。

新新闻立项用 validate_news_focus 限制任务书负担；旧任务书读取保持兼容。

主编在选题任务内主动补证，提交报道或不报道。报道时交接唯一报道角度、可选事实边界和工作标题。历史决定和旧任务冻结契约保留原值。

newsroom-v8及后续新任务由冻结editorial_policy控制传播校验；目录版本用于资源发布，不承担策略开关。v7历史快照有兼容分支。
