# 共享资料

当前普通页面在显式确认时检查项目inbox、Chrome下载偏好目录和Windows下载目录，匹配后复制，原文件保留；自动浏览器仅保留诊断实现。宿主目录发现与扫描分别位于infrastructure/persistence/manual_pdf_directories和manual_pdf_inbox，客户端不能指定路径。`manual_pdf_imports` 拥有确认契约和精确匹配/歧义/幂等用例；只通过绑定回调连接精选论文与导入端口。

历史下载端口为 paper_downloads.PaperDownloader / PaperDownloadLedger；默认 DailyBrowserDownloader 在持久额度内通过日常 Chrome 获取个位数清单，人工导入同样校验主文身份及双哈希。ScanSci 运行适配器已删除，下文相关历史观察不代表当前实现。

`browser_paper_service.py` 拥有绑定论文的状态／获取用例；只接收宿主请求，失败同样缓存，模型不能改变浏览器步骤或自动重试。DSH 的专用 MCP 与角色配置通过 composition 装配，通用浏览器能力留在基础设施中。

`SourceRecordSnapshot` 是已归档来源版本的公开读取契约；RssSignal 保留实际字段文本、出处及信息状态，不表示完整摘要。其他业务通过资料仓储公开快照消费，不直接查询内部表。

定义被获取的资料、来源版本、原始响应和稳定身份，是采集与内容消费之间的公共语言。

- 对外数据：collection_records 的 CollectedRecord、CollectionPage、SourceResponse、PersistedRecord。
- 纯规则：material_identity.identity；来源时间及其精度保持原样。
- 资料、来源版本和文件的具体保存由 material_repository 实现，页事务由 collection_page_repository 协调。
- 新增资料身份规则在这里实现并测试；候选价值判断归 discovery，最终稿件及引用规范归未来 content。
- 发布者、采集来源、作者单位和报道对象不能混为同一字段。
- CollectedContent/CollectedLink 表示实际正文、预览或截断文本及引用。新增可选内容不改变旧连接器的版本指纹；通用采集按稳定外部身份保存资料，按内容变化追加来源版本。

## 论文下载

`paper_download_jobs.py` 拥有人工任务、进度、浏览器连接生命周期与仓储端口；连接地址只用于进程内当前任务，不进账本。`PaperDownloadJobRepository` 保存可恢复状态；跨模块的冻结清单校验、单篇执行及导出由 `PaperDownloadWorkbench` 协调，线程和 HTTP 生命周期归入口。`PaperBrowserControl` 只提供连接／状态／关闭，不向业务或模型暴露通用脚本。

`paper_downloads.py` 拥有逐篇下载请求和真实尝试结果。文件账本在 `data/paper-downloads/attempts`，每次尝试独立、原子落盘；历史不覆盖，最新视图按判别 ID 合并。网络和插件进程不持有数据库事务。`PaperDownloadRepository` 提供保存、最新结果和原始历史入口；不扩大集中存储门面，也不修改发现批次分流。

Chaoslib 是当前下载基础设施适配器，账号预算与封禁状态在私有目录持久化。DSH 情报工具 `download_primary_paper()` 不接收网址/路径，只读取个位数清单中属于当前任务输入、已由宿主取得的论文。可复用实测结果；正文必须通过文献身份、PDF 与文本哈希检查。报告附件、身份未知和技术失败均不能冒充研究全文。

来源“本批次有实测可下载文章”由账本的 source_id、批次、渠道与成功数投影，代表观察，不代表期刊全部文章可下载，不影响 RSS 可用性标记。新增下载渠道适配放基础设施；下载结果契约在资料模块，主题价值判断仍归 discovery。详见[接入说明](../../../../docs/paper-selection-pipeline.md)。

- `SourceRecordReading` 是按归档来源版本读取的正文/摘要契约；区分全文、片段、摘要、链接、受限和缺失文件，分页限制正文大小。批次成员授权由发现查询协调，资料仓储不发起网络或模型请求。

- `document_reading.py` 拥有绑定已校验原文的分页解析契约；DSH 只读 MCP 不接受任意路径或上传策略。MinerU CLI 位于基础设施，装配与文件账本验证归 composition，见 [接入说明](../../../../docs/mineru-provider.md)。

`document_reading` 的 DocumentReference、DocumentEvidenceRead 和分页读取端口，以及 `evidence_catalog` 的无正文目录投影，是供 discovery/content 使用的公开资料契约。调用方不能借此读取资料内部表或扩大文档范围。模型初始上下文只给目录，正文一律显式按需读取；不预取整篇论文。
