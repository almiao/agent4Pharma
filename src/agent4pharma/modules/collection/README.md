# 来源采集

带 rss_profile 的期刊来源按该版本提取信号入库；采集之后本地预选由发现模块负责。collect-only 包含已启用的本地判别，但不会调用远程情报模型；待补证不等于采集失败。

将来源查询窗口推进为持久化资料和覆盖状态，支持分页、断点恢复与失败后续跑。

- 用例入口：source_collection.CollectionService.collect_source。
- 外部契约：acquisition_interfaces 中的 SourceConnector、CollectionPersistence、SourceAccessPersistence。
- 依赖资料契约和运行配置，不依赖 HTTP、SQLite 或 DSH 的具体实现。
- 来源解析归 infrastructure/connectors；跨业务日调度归 runtime；多个模块调用顺序归 workflows。
- 新增来源优先复用本用例，不能复制状态机。资料、批次成员和检查点必须原子提交。
- SourceError 表示失败；合法空结果与失败分别表达，PaginationChanged 要求重置分页。
- 通用媒体通过同一 SourceConnector.page 契约运行，首个检查点保存发现队列，随后逐篇提交。deferred_error 表示补抓队列已保存，本轮结束为 partial；resume 使用旧批次冻结规则。
- RSS 元数据计划直接提交标题、简介、日期原值与原始 Feed；有时区的时间按窗口过滤，日期不完整的记录保留且不虚构时区。元数据入库不代表取得全文。`--collect-only` 依据 coverage_status 判断到期/退出，不能把待精筛误报为采集失败。

- CollectionDiagnostic 保存一次失败复核的来源、URL、记录时间和 HTTP 状态，与原错误分开；诊断不是采集进度，不将失败变成零结果或部分成功。
