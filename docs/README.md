# 当前文档入口

更新：2026-11-07。日常实现从本页进入。保留当前操作、工程规范和有效架构决策；历史归档、实施流水账及失效决策正文已清理，旧 ADR 编号通过去向表解析。

| 要做的工作 | 优先阅读 |
|---|---|
| 新机器安装、依赖与 GitHub 提交范围 | [安装与仓库准备](installation.md) |
| 工作台功能与边界 | [功能状态表](console-migration-matrix.md)、[运行台当前状态](workflow-management-console.md) |
| 工程结构与约束 | [代码架构](code-architecture.md)、[实际模块布局](refactor-layout.md)、各模块 README |
| 已确认业务决策 | [有效 ADR](architecture-decisions.md)、[主题范围](research-scope.md)；旧编号查 ADR 末尾去向表 |
| 界面开发与构建 | [界面准则](interface-design-principles.md)、[前端说明](../frontend/README.md) |
| 交互响应与接口耗时 | [UI 性能目标与验证](ui-performance.md) |
| 情报筛选与 PDF | [筛选流程](paper-selection-pipeline.md)、[本地摘要筛选](local-rss-triage.md) |
| 来源采集与配置 | [通用媒体采集](configured-media-collection.md)、[RSS 采集](journal-rss-collection.md)、[来源目录](shared-source-registry.md) |
| 当前数据口径 | [节点数据](run-node-data.md)、[指标维度](run-metric-dimensions.md) |
| 内容制作与准确版本 | [生产操作](production-user-guide.md)、[制作契约](editorial-production-contracts.md)、[阅读评审](reader-editing.md)、[稿件编辑](article-editing.md) |
| 图文预览与导出 | [图文制作](publishing-layout.md) |
| 通用执行追踪与输入输出 | [执行追踪](execution-tracing.md)、ADR-073 |
| 采编节点上下文 | [当前运行契约](production-context-contract.md)、[整体业务设计](production-context-design.md)、[代码改动方案](production-context-code-plan.md) |
| 技能资源与质量回归 | [新闻技能](newsroom-skills.md)；旧单篇回归已退役，见ADR-083 |
| 研究、写作与图文改造对照 | [编辑改造对照验证](editorial-comparison-evaluation.md)，真实实验尚待执行 |

这些文档覆盖不同责任，不以一篇无限追加的进度日志替代。当前能力以专题契约及最新有效 ADR 为准；功能状态表用于导航。后续更新直接修改对应规则，不向索引追加逐轮实施日志。
