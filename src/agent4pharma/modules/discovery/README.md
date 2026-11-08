# 候选发现

`processed_papers` 从正式成功主文关联的账本公开契约生成精确身份键，供新筛选运行避开已处理论文；
不修改旧池、不按标题猜测。`paper_selection.manually_select_paper` 保存独立人工精选标记，
`require_pdf_association` 允许同轮论文人工关联，自动获取继续使用 `require_shortlisted_paper`。

摘要→个位数全文的契约在 `paper_selection`，新闻比较契约及校验在 `news_selection`，机构关注名单与别名匹配在 `news_preferences`，轻量投影在 `paper_selection_view`。当前编排为 `workflows/select_local_papers` → `compare_paper_news`：明确作者单位命中关注机构、重点期刊依次优先，同档按新闻价值固定前10，再整体商业比较最多5篇（可零篇），人工下载；普通期刊与预印本同档。基本主题作门槛，机构未知正常参与。摘要阶段禁止全文调用，不写正式候选；旧最多9篇及逐篇复核保留历史。专用 `PaperSelectionRepository` 保存冻结输入、名单、提示词、模型身份、比较结果和追加版本，不扩张集中存储。见[流程说明](../../../../docs/paper-selection-pipeline.md)。

运行投影契约在 run_records：来源覆盖、完整来源成员、成功分流与技术失败分开，不能按 DISTINCT 模型缓存估计文章数。evidence_status 仅从实际证据留档推导摘要/全文取得状态。明确父运行查询不受全库最近列表截断；不按标题或同日补造关联。

全来源本地预选由 `local_triage.LocalTriage` 和 `triage_contracts` 拥有，固定输入/提取/模型/准则身份，成功结果可复用；失败保持技术重试，不是业务否决。新版二选一：analyze 进入情报队列，exclude 不进入；不生成第三路或直接生成正式候选。非 RSS 资料用原生摘要作为判别文本。旧三分类只读历史保留，SQL 由专用 RssTriageRepository 实现（表名保留历史）。

管理发现批次的候选状态、受限资料访问、精筛摘要与报告；不决定正式立项或发行。

- 业务入口：batch_service.DiscoveryBatchService；CLI/MCP/工作流复用这里的校验。
- 持久化契约：batch_repository.DiscoveryBatchRepository；结果展示：batch_reports。
- 检索词表与查询编译：screening_policy；本地预选：local_triage/triage_contracts；正式语义契约：intelligence_contracts；历史评估契约仅用于只读与旧快照解析。关键词不再过滤已采集资料，随机抽样模块已删除。
- 候选摘要与关注理由由精筛基于实际证据生成；模型不能修改来源覆盖或既有入选依据。
- 当前模型调用为 infrastructure/llm/local_decider、news_comparison；编排为 select_local_papers、compare_paper_news。

- 后台情报列表通过 IntelligenceStore.recent_summaries 读取有界元数据，正文仅在按任务读取详情时返回；页面导航不改变任务状态或证据所有权。

- 已知预印本/PubMed 采集提供原生摘要时，来源版本通过 source_abstract 传入任务，情报工具直接复用并留档；缺失或占位才补取。不能因外部摘要库未索引而否定来源自带摘要。

- 本地归档汇批通过 sqlite/archive_batch_repository 工作单元新增批次：仅取来源/外部 ID 最新版本，保留原证据、旧批次与 demo 身份；操作入口见 scripts/consolidate_archived_records.py，不执行模型或重新采集。

- 用户明确授权时可用 retained_batch_repository 并回补采及清理无引用历史；保留被评估/制作/回归引用的批次和 raw_refs 闭包，不删除来源、策略或稿件。

- run_duplicates 只标注本批同资料多来源及 already_seen 复用；后者可忽略，更新版本和待重评不建议直接忽略。标记不修改处理状态。
# 显式全量重跑

全部运行数据重置的持久化适配器为 `DiscoveryResetRepository`：只从备份复制目录与规则，不原地改写历史。维护边界见 `docs/fresh-discovery-run.md`。历史专项全量复采脚本已移除，新采集与筛选使用正式工作台的日常执行入口，不绕过暂停计划。

2026-11-04：旧逐篇摘要/全文评估、终判校验、DSH/MCP执行器及其维护复测与单篇回归执行已删除。
`intelligence_contracts` 保留历史 JSON 和当前精选输入的兼容契约；IntelligenceRepository 仅只读。
新运行以当前共用主题标准冻结输入身份，不初始化旧情报准则、任务或候选；旧输入不能恢复旧执行链。

当前摘要用本地Decider分类及独立新闻价值排序问题；`select_local_papers` 固定前10，由 `compare_paper_news` 整体比较最多5篇供人工下载。大机构大厂仅有实际原文或作者单位依据才加分；未知单位不设排除门槛。无摘要使用标题和已有来源文本，模型技术失败保留断点；旧运行仅供历史回放。新增新闻判断及比较规则放 `news_selection`，网络适配位于 infrastructure/llm/news_comparison，原生排序端口在 `triage_contracts`。

2026-11-02 人工推荐边界（覆盖旧全文终判交接）：人工在论文精选中明确确认并关联有效主文后，`human_recommendation` 绑定来源项、主文ID/hash、确认时间及版本。`workflows/imported_paper_recommendation` 生成制作侧快照，assessment_id 为空，不伪造模型结论或已读全文。后续直接走论文理解/背景/主编/写作/核验；问题由对应节点退回。历史全文评估不改写，启动恢复不为历史导入自动回填人工确认。单纯下载或关联文件不授予模型执行权限。

paused_journals/paused_items由paper_selection拥有，按明确期刊元数据暂停新筛选输入，不能只用采集source_id阻断PubMed转引；旧运行冻结名单不改。当前NEJM AI依用户决定暂停使用，恢复另建新运行。

- paper_selection_queries 的只读运行目录与 paper_selection_view 的可裁剪投影服务精选页；完整统计保留，裁剪不重做推荐，冻结请求不被展示状态修改。

排序与比较规则支持设置页news_ranking/news_comparison版本编辑；新运行冻结正文/示例及版本身份，按批次采用新发布内容时另建运行，指定运行续跑不替换。程序协议、优先级和容量保持不变。

- daily_batch_runs拥有显式日常跑批尝试、状态及结果契约；workflows/daily_batch_execution串行协调既有用例，专用DailyBatchRunRepository原子保存元数据。同源POST授权当天执行，GET只读；UUID和活跃尝试防重，重启中断不自动补跑。见ADR-097。

- 精选失败恢复使用RetryPaperSelection准确运行契约，经日常串行执行器复用冻结输入和本地结果；商业传输短ID双射不改变真实来源身份和严格校验。NewsComparisonValidationError/NewsComparisonRequestError只包含可公开的宿主诊断。见ADR-099。

- 商业引用传输协议evidence-ref-v1由适配器从本篇原文生成有界引用片段，选择编号后还原并继续严格校验；comparison_transport_version留实际协议版本，缺失保持未知。可定位引用不代替语义审阅。见ADR-100。
