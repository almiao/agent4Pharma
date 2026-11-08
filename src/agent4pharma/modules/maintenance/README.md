# 情报维护

管理用户提交的文章样本、调查任务、引用证据与人工判断；不自动发布来源或修改每日采集规则。

- models：公开输入输出及运行状态；repository：持久化契约。
- investigation_tools：有页面预算和证据约束的调查工具，规则流程与 DSH Agent 共用。
- 来源目录由 sources 管理；筛选主题由 discovery 管理；跨步骤运行位于 workflows。
- 手动资料不计作自动采集成功，候选原始来源、实际已读页面和模型推断分别展示。
- 网络/SQLite/DSH 的具体实现在 infrastructure，界面不直接执行 SQL。
