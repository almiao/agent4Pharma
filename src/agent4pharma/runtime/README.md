# 执行基础

context_records定义通用公开上下文清单、尺寸、规范化hash和非凭证回执；不导入业务类型，不代表供应商HTTP报文或模型理解。业务授权与工作集投影属于workflows，SDK显式关联属于基础设施适配器。

提供启动配置、到期日期计算、互斥和执行能力契约；不承载文献筛选或内容审核规则。

- settings：ApplicationSettings/ModelSettings，配置校验、路径解析和策略快照。
- scheduling：08:00 到期与追赶日期计算；locking：同一采集目录的进程互斥。
- execution_interfaces：资源关闭、任务记录与模型运行契约；具体 SQLite/DSH 在 infrastructure。
- 新增通用任务能力在这里定义契约；领域任务的成功、阻断和取消条件由对应业务模块决定。

- execution_trace/execution_recording：业务无关的执行身份与安全输入输出记录；框架适配归infrastructure，业务不另建trace格式。WorkflowObservation.execution可选，旧记录不回填。

- production_cost：制作节点的当前官方价估算、时段与未知状态；不处理充值、账单或扣费。
- production_failure：按明确模型结束代码提供安全公开诊断，不扫描正文猜HTTP错误。
