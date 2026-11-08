# 作者研究底稿

先围绕任务书研究并调用save_writing_research，再在同一任务继续写作。底稿不是最终稿，不使用submit_reporting。

| 字段 | 要求 |
|---|---|
| question | 5–200字符；作者从 angle 推导的本篇研究问题 |
| answer | 20–1600字符；回答本篇question，解释关键做法、结果及直接意义，所有事实前提须由下列证据支持 |
| findings | 1–8项ResearchFinding；只取服务选题的论文事实，保留对象、条件与数字，附真实evidence_id和source_span |
| background | 0–5项ResearchFinding，kind=background；解释所需背景及真实来源，可以为空，标题不冒充事实核实 |
| gaps | 0–5项{question, handling, reason}；handling为omit（省略）、qualify（限定）或block（核心前提无法成立） |
| research_scope | 实际已读、已查范围和技术失败；未独立复现、未读范围不当作研究缺陷 |
| claim_check | 可选；核心报道主张、支持范围及会改变判断的已查条件；依据仍放在下列事实引用中，未知放gaps |

ResearchFinding可选字段：narrative_use为main_result/explanation/boundary/context/omit，selection_reason说明对主线的用途或取舍原因；source_relation为original_research/team_explanation/external_commentary/unknown。只对影响选材或来源理解的材料记录，不按填写数量验收。外部评论放background并明确归属。read_bound_source获取的搜索结果正文仍属于背景，不能放入findings冒充主论文结果。

source_span使用实际取得的content_hash/start/end，连续范围须覆盖对应陈述。网页片段不能作为论文核心结果的依据。

expected_version首次为0；get_production_context.writing_research给出现有版本，实质更新时传该version。宿主绑定story_id、任务书版本、angle 及保存时间，不由模型填写。相同内容重复提交幂等。

先检查已有材料，已经回答的不留待查。一般未知通过省略或限定解决，不以未来科研验证要求阻断报道；block仅用于核心角度无法成立，此时报告具体阻断，不提交正文。

继续/重试、局部返工复用已有底稿，不机械重做调查。修正结论时核对原证据并追加底稿版本；编辑会取得交稿时准确底稿及来源。

## 新闻骨架 outline（news-outline-v1）

新任务起草前必须保存outline；研究过程中可先保存不带骨架的底稿，但不能交稿。字段：

- mainline：承接任务书的新闻主线；progression：整体结构及理解推进顺序；ending：最终回答到哪里。
- paragraphs：按计划自然段排序的非空列表，不把小标题算段落；每段记录question（本段回答的读者问题）、message（新增理解）、position_reason（安排理由）。
- section：仅在计划分节的首个自然段填拟用小标题，直接填标题文字。导语、同节后续段及无需分节的正文留空。多个自然段可以共同回答小标题引出的问题，各段仍分别记录自己的question。
- 每段sources为实际依赖材料：evidence_id、source_span、location、use。论文位置复制已登记read_scope或一个locator，保留PDF页码口径；外部调研依赖已登记网页正文或检索片段，location复制read_scope或url，use说明实际结果及在本段的用途。材料没有任何位置元数据时填“已绑定文本片段”，不猜页码。source_span仍复制实际hash/start/end。
- 纯过渡/归纳段sources可空，但depends_on须引用至少一个前文计划段号（从1开始），不得前向、自引或循环；新增事实仍须有sources。
- visual_need（段落级）和omissions（整篇）可选，分别说明解释配图需求与重要材料的取舍。

不重复抄写论文原文，不为每段额外搜索。相同材料可支持多个段落。可与研究底稿一起保存，随后直接继续写全文；微调措辞、拆并段或相邻顺序不强制重存，实质改变结构、关键判断或材料依赖才追加版本。历史冻结任务没有此策略时不补写骨架。骨架不能代替正文claims或独立审核。
