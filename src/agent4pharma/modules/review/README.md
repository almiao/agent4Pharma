# review 责任卡

- review_issue_scope提供返修读取与交稿共用的有效问题集合和准确报告范围hash，历史保留原规则，新window-v1使用最新权威报告、报告/稿件身份及有序开闭状态；不把作者回应当作关闭，不执行任务状态迁移。

- 拥有：准确版本审核、问题单及复审语义。
- 公开入口：review_contracts 的类型与 validate_review；应用交接由 workflows 消费，不读取其他模块内部表。
- 依赖：标准库/Pydantic；不依赖 SDK、网络、CLI、Web 或持久化实现。
- 专用仓储位于 infrastructure/persistence/sqlite/production_repository.py，工作单元统一提交；不是集中存储门面扩展。
- 新需求放置示例：新增审核问题级别在 review_contracts；不得由作者或主编直接改审核通过。

news_acceptance.validate_news_acceptance 校验新闻必改分类与定位；不替模型判真、不自动通过。

阅读品质由reading-review独立任务承担，ReaderReviewReport/reader_reviews保存准确版本结论；两项方法共用一次阅读，新任务默认5次、最多10次的全流程共用作者返修额度，事实核验仍独立。新链reader-quality-v1，不回填旧历史；详见docs/reader-editing.md与ADR-101。

ADR-103：ReaderReviewResult与ReviewResult为新工具提交契约，仅允许通过/返修；历史ReaderReviewSubmission与ReviewSubmission保留旧枚举用于报告读取。validate_reader_review/validate_review同样拒绝第三种新提交。blocking只表示严重性，仍以changes_requested要求作者处理；各阅读维度由准确必改问题确定通过/返修。
