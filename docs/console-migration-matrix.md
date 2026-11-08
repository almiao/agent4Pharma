# 工作台功能状态与边界

更新：2026-11-06。本页保留当前功能定位，替代迁移前盘点和逐轮实施日志。正式入口为 `/console`；`view=observe` 是样例，不能作为生产完成依据。业务规则以最新有效 ADR 和专题契约为准。

| 范围 | 当前状态与约束 | 详细说明 |
|---|---|---|
| 工作台与导航 | React + Ant Design 为唯一正式后台；旧地址只转换准确对象和筛选范围，不渲染旧页面。来源与规范页共用导航。 | [运行台](workflow-management-console.md)、[前端](../frontend/README.md) |
| 来源采集 | 配置驱动采集，保留来源身份、资料、游标与失败；配置保存和检索预览不授权采集或模型。 | [媒体采集](configured-media-collection.md)、[RSS](journal-rss-collection.md)、[来源目录](shared-source-registry.md) |
| 论文精选 | 本地 Decider 预选并独立排序前十，商业模型整轮比较最多五篇，再人工推荐和取得主文。无摘要仍按已有材料处理；NEJM AI 暂停新筛选和新推荐。 | [筛选流程](paper-selection-pipeline.md)、[本地排序](local-rss-triage.md) |
| 主文获取 | 新筛选运行跳过已有成功主文关联的论文，旧运行保留。价值排序可人工加入精选；精选建议下载优先，未获建议也可关联 PDF，DOI 可复制。单纯关联不调用模型，确认推荐并启动采编才请求执行；自动获取仍限冻结下载清单。 | [筛选流程](paper-selection-pipeline.md) |
| 采编 | 主编选题 → 作者研究与写作 → 阅读评审 → 事实核验 → 图文制作 → 人工定稿。四角色为 editor、writer、reviewer、publisher；阶段为 topic-selection、draft-writing、reading-review、fact-check、layout-production，协作改稿为 article-revision。 | [生产操作](production-user-guide.md)、[制作契约](editorial-production-contracts.md) |
| 作者研究 | 同一写作任务保存版本化研究底稿和有证据依赖的新闻骨架；不另排研究调用。新稿提交绑定准确任务书及冻结契约。 | [新闻技能](newsroom-skills.md)、ADR-115 |
| 评审与返修 | 阅读分别展示新闻吸引力/阅读推进、中文自然度结论，事实另评。各评审仅通过或要求返修；全流程共用默认五次、最多十次作者返修额度。额度耗尽停止自动调用，保留返修结论；工具异常单列。 | [阅读评审](reader-editing.md)、ADR-102/103 |
| 执行与恢复 | 启动、重做、继续和重试即请求调用模型，共用串行执行器。仅恢复显式持久化 auto_run 意图；继续/重试复用冻结配置，重做另建根任务并保留历史。 | [生产操作](production-user-guide.md)、ADR-085/089/092 |
| 节点上下文 | 新根任务冻结node-context-v2及revision-window-v1：返修候选、小步校验、按需资料/内容去重，活动窗口替换与压缩后恢复；历史不升级。全周期费用只读汇总全部尝试与后继。 | [运行契约](production-context-contract.md)、ADR-118/119/120 |
| 任务与详情 | 当前/历史集合和计数共用对象范围；最新制作轮次沿人工推荐身份定位。准确阶段提供暂停、停止、继续、重试；未立项研究仍沿真实链展示。 | [运行台](workflow-management-console.md)、[节点数据](run-node-data.md) |
| 人工审稿 | 准确图文版本支持手动编辑标题/正文/图注并显式保存，也支持选中引用、批注和DSH定点修改；共用撤销与直接人工定稿，不再要求修改后模型核验。新包不沿用旧批准；不恢复人工任务书编辑或补证分支。 | [稿件编辑](article-editing.md) |
| 图文与交付 | 图文绑定准确稿件、图片和引用；人工批准后可导出或交付公众号草稿箱。草稿箱交付有指纹校验、防重和未知结果人工核对；不等于群发或公开发布。 | [图文制作](publishing-layout.md) |
| 规范与提示词 | 草稿、发布、版本比较和任务冻结；排序与整体比较分别使用 news_ranking/news_comparison。保存或发布不授权执行。 | [新闻技能](newsroom-skills.md)、[运行台](workflow-management-console.md) |
| 依赖服务 | MinerU/Decider 只能通过白名单同源 POST 显式启动；不自动下载模型、不接管外部进程。 | [MinerU](mineru-provider.md)、ADR-079/086 |
| 追踪、用量与性能 | 使用通用调用身份和输入输出；缺失历史不补造。用量缺失标未知，费用为估算；普通交互以 200 ms 可见响应为目标。 | [执行追踪](execution-tracing.md)、[性能](ui-performance.md) |

## 仍需区分的边界

- 旧逐篇摘要/全文精筛、来源维护复测和单篇回归执行入口已退役（ADR-083）；历史数据读取不等于仍可启动旧流程。
- 旧后台、人工管理选题/任务书、人工粘贴补证及旧孤立任务兜底已移除，不因文档清理恢复兼容层。
- 数据结构、角色或阶段契约变更后，常驻服务须在满足相应维护条件后重载；文件变更不证明生产已切换。ADR-115 的生产切换清理须保护已有准确批准包和交付防重依据。
- 离线测试不证明真实模型生成质量；[编辑对照验证](editorial-comparison-evaluation.md)仍是待执行方案。上游分主题召回覆盖也不能由单篇通过推断。
- 本页不保存历次测试数量、机器端口和样本运行日志，不把过去一次验收表述为当前运行证明。
