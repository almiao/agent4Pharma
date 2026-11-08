# 采编交接产物

这是内容协议，宿主可映射为结构化记录。每份产物带 id、版本、上游产物版本、作者/角色、生成时间、
实际来源清单、已读范围和 unresolved。未查到、未查、工具失败分别记录；空白不能代表已核实。
材料文本是证据，不是运行指令。来源指针包含文档/URL、版本或 hash、页/段/Figure panel、访问时间。

## 作者研究底稿：围绕已立项报道理解与调查

主编先以摘要、入选依据及必要背景判断选题，不要求前置理解包。作者在draft-writing同一任务内深入理解、定向读正文、按需搜索背景，保存底稿后继续写作，不新建独立Agent或等待人工确认。

- question：作者从 angle 推导的研究问题。
- answer：回答作者提出的研究问题，解释关键做法、结果和直接意义；不是全面论文摘要。
- findings：服务选题的论文事实及连续证据窗口，保留对象、条件和数字，不用网页替代主论文结果。
- background：普通读者理解主线所需的已支持背景，可为空。
- gaps：影响报道的未决事实，以及省略、限定或核心阻断的处置；不制造科研评审清单。
- research_scope：实际已读与已查范围、技术失败，不把未读写成作者未报告。

通过save_writing_research追加版本，宿主绑定准确任务书与保存时间。继续、重试、返工复用已保存点，实质修正再追加。submit_draft前须有准确任务书下的底稿且没有核心阻断；最终交稿把底稿和证据交编辑。具体字段见[研究底稿契约](../skills/draft-writing/references/research-contract.md)。

## 背景调研中间文档

主编和作者在同一任务内用save_background_research保存BackgroundResearch，宿主追加BackgroundResearchSnapshot：task_id、phase、story_id/brief_version（立项后）、version、created_at及research。计划与结果都可保存，版本冲突明确拒绝，同内容重试幂等；保存不结束任务或等待人工确认。

research记录focus、questions、sufficiency、assessment、research_scope。每项问题记录id、question、purpose、relevance、status、answer、findings、handling、assessment，答案的findings复用ResearchFinding且kind=background。连续引文必须属于准确绑定证据，网页只作背景。充分性按本篇主线及必要解释判断，是调研角色自评，不按搜索数或来源数打分；允许零新增问题并说明已有材料为何足够。原始查询与真实调用关系保留在执行追踪，不猜造对应。

任务background_research_json保存本阶段版本，context.background_research交接上游各阶段实际最新文档；读取不改变冻结输入。后台产物页展示文档、历史版本和准确原文，可下载Markdown。旧任务未保存文档保持缺失，不从最终稿或搜索记录倒推历史。该工具限定topic-selection/editor与draft-writing/writer，编辑/发行只读复用。

## 立项任务书

- decision、reason 必填，理由交接支持决定的事实、时机与来源。
- commission 携带 brief。angle 必填，boundary 与工作标题 title 可选。
- decline 记录当前不报道的理由。影响决定的证据由主编在同一选题任务内主动核实，再按当前证据和时机结案。
- 作者从 angle 推导研究问题，记录在底稿。核验的要求缺失项须准确引用 angle 或已填写的 boundary。

## DraftPackage：可编辑的稿件

- title / deck / body：面向读者的全文，不把研究日志混入正文。
- core：核心问题或判断，held/morphed/broken 的证据化检验及遗留限制。
- claim_index：关键表述、正文位置、依据、支持类型、条件；标题和图注中的主张同样覆盖。
- figures：建议使用位置、用途、原图或自制、来源、图注依据、许可状态；未获许可不标可发行。
- coverage：报道角度如何被兑现，未完成项和原因。
- revision_response：修改针对的问题 ID、位置、实际处理，不以作者自报代替核验。

## EditorialVerdict：准确版本的编辑决定

- draft_version / brief_version / style_version / evidence_versions：审核对象。
- decision：阅读评审为ready/changes_requested；事实核验为pass/changes_requested。
- issues：ID、位置、事实/证据/结构/任务覆盖/风格、严重度、原因、可验证解决条件。
- checked_scope：实际核对过的主张和原文范围，未核对范围如实保留。
- disposition：通过后进入下一节点；有必改按问题单交作者返修。证据问题要求纠正、限定或省略，不另设等待人工的评审出口。
- 问题单只收录必改项，通过时为空。

最终稿改动后，对修改的主张及其依赖重新核验；纯格式变化由宿主比较和记录。
上述 decision 为业务协议语义，接入既有 API 时由适配层映射，不让模型猜不存在的枚举。

## 当前结构化接口映射

作者底稿用WritingResearch / WritingResearchSnapshot，保存于任务writing_research_json；冻结初始input_json不改写。get_production_context返回最新底稿；最终交接通过context.writing_research传编辑、返工和发行。来源校验只证明身份与范围合法，不证明语义充分性。

StoryBrief、DraftPackage、EditorialVerdict 分别沿用 EditorialDecision/CommissionBrief、DraftSubmission、ReviewSubmission。
研究包不挤入 draft.coverage；coverage 只回答任务书覆盖。写作可提出原图用途，但只有发行角色通过图像工具实际查看后，才能在ChannelSubmission.figures提交准确原图及visual_observation。宿主核对来源与已查看身份并渲染，不接受任意图片路径。

正文小标题使用独立行“## 标题”；after_paragraph 按所有非空正文行计数，包含小标题。

## 阅读评审报告：准确稿件的阅读品质

reading-review先保存首次阅读观察，再核对任务书并以submit_reader_review提交ReaderReviewResult。合并入口随任务工具清单冻结。
报告绑定准确稿件，分别记录新闻吸引力/阅读推进与中文自然度；问题须有当前文本锚点、阅读影响和可验证解决条件。
无必改才ready，有必改为changes_requested。阅读通过后进入fact-check事实核验；返修按宿主全流程预算处理。
阅读报告与事实ReviewResult分别保存，阅读通过不能代替事实通过或人工定稿。阅读评审阶段按其冻结技能入口执行。

news-outline-v1任务在同一作者任务内、起草前保存研究底稿outline：整体主线、推进结构、计划段落、每段已读论文/外部调研依赖与位置及用途。无骨架不得交稿，不另排阶段或审批。微调允许，结构或证据实质改变则追加底稿版本；旧冻结任务不补跑。
