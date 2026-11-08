# 内容生产：契约、状态与持久化设计

当前阅读品质链与新增准确版本契约见[阅读评审](reader-editing.md)、ADR-101；下文历史总体契约未逐项实现的部分不能视为当前功能。ReaderReviewReport独立于事实ReviewReport，新稿的reader_policy决定是否必须通过阅读验收。


立项任务书交接唯一 angle、可选 boundary 和工作标题 title。作者在底稿中提出研究问题，宿主将底稿绑定到准确任务书版本和角度。事实核验的要求缺失可直接引用 angle 或已填写的 boundary。写作和事实核验按缺口读取已绑定的原文片段或 PDF 页窗，优先复用缓存。

## 当前采编流程（2026-11-04，ADR-092）

主编选题（摘要与必要背景）→作者研究与写作→编辑核验→图文制作→人工定稿。
不再先运行独立论文理解或背景阶段。作者在同一写作任务内先保存基于选题的研究底稿：
中心问题的回答、论文事实与出处、必要背景、未决问题及省略/限定/阻断处置。保存后继续写作，
不新排研究Agent、不等待人工确认；编辑与返工复用底稿。主编与作者可有界搜索背景。
继续/重试保留已保存底稿，仍从新的模型会话执行；未提交正文不能冒充保存点。
本条覆盖下文旧理解链与作者无搜索权限的历史说明；具体契约见[新闻技能](newsroom-skills.md)。


> 2026-11-03：ADR-071取消active选题名额限制，制作按阶段任务及已提交产物保存点恢复，当前行为见生产操作说明。

> 2026-10-30：schema v7 已实现内部制作闭环的子集；[生产操作说明](production-user-guide.md)区分真实契约与本文目标设计。本稿所述 schema v6 是设计时基线；外部发行意图/outbox/授权尚未接入。

以下总体契约源于早期设计，未逐项实现的字段和接口不构成当前 API；当前流程按最新有效 ADR 及专题操作说明执行。

各模块提供窄查询/操作契约；前端不直接跨表联查，不维护另一套状态机。运行台边界见[功能状态表](console-migration-matrix.md)。

## 1. 模块所有权与依赖

| 模块 | 拥有的数据与用例 | 允许消费的公开能力 |
|---|---|---|
| discovery（已有） | 评估、候选、更新；现有候选历史保留 | materials、sources/collection 的契约 |
| editorial（拟建） | 编辑决定、选题任务书、优先级、容量、重大变更 | discovery 候选只读投影、content/review/publishing 状态投影 |
| content（拟建） | 稿件、修订、表述与证据映射、素材清单、作者回复 | editorial 任务书快照、共享资料获取/读取 |
| review（拟建） | 审核任务、问题单、复议、精确版本结论 | 不可变任务书/稿件/证据包，只读必要原文 |
| publishing（拟建） | 渠道包、发行授权、意图、outbox、回执与更正 | 审核合格证明、稿件/素材包、渠道适配端口 |
| runtime（已有，按需扩展） | 执行任务、租约、预算账本、重试计划 | 受限调用角色运行器，不拥有选题规则 |
| workflows | 跨模块调用顺序、补偿与投递恢复 | 上述模块的公开用例；不直接读写业务表 |

“主编待选池”现有记录仍归 discovery，editorial 保存处理决定并生成队列投影，不迁走或复制候选表。反向反馈通过事件/命令交接，不做模块双向 import；需要另一模块状态时使用消费方定义的窄端口，由 composition 注入实现。

未来包有真实实现时再创建，每包先写责任卡。建议从 `editorial/commission_service.py`、`commission_contracts.py`、`commission_repository.py` 开始；不生成空包，不扩张 SqliteApplicationStorage。外部契约用 Pydantic，内部按需 dataclass；不把 SDK 类型或裸 dict 作为新业务模型。

## 2. 公共字段与错误语义

可修改聚合都包含 id、revision（单调递增）、created_at/updated_at（UTC）；业务日期按 Asia/Shanghai。来源只有日期时另存精度，不补造时区。

每个写请求包含 request_id、expected_revision、idempotency_key；上下文由服务注入 actor_identity、task_id、访问范围及授权引用。不可变版本有 content_hash 和父版本引用；hash 校验一致性，不证明事实真实。

统一错误语义：NotFound、VersionConflict、ScopeDenied、InvalidTransition、BudgetExceeded、EvidenceUnavailable、ReviewStale、AuthorizationRequired、DeliveryUnknown；具体 Python 异常在实施时归所属模块。返回成功空列表、partial、blocked 和执行失败分别表达，入口不得吞错。

重复幂等键加相同请求 hash 返回原结果，重复键但不同请求体拒绝冲突。版本冲突先重新读取，不自动把旧模型结论套到新输入。工具返回小摘要及可分页产物引用，不一次注入全稿库。

## 3. 核心记录

| 记录 | 必要字段与不变量 |
|---|---|
| CandidateRef | candidate_id、assessment_id、material_version_refs、policy_refs；复用已有 ID，不以标题作身份 |
| 主编提交 | candidate_ids、decision（commission / decline）、reason；仅 commission 带 brief。主编在本任务内补查影响决定的事实，按当前证据和时机结案。历史决定保留原始取值与条件。 |
| CommissionBrief | angle、可选 boundary 与 title；选题身份和版本由宿主持有 |
| ChangeRequest | story_id、base_brief_version、requested_change、reason、evidence_refs、resource_delta、impact、status；内容暂停依赖该变更的部分 |
| DraftRevision | story_id、revision_id、parent_ref、brief_version、body_artifact_ref、body_hash、evidence_manifest_hash、asset_manifest_hash、model/prompt_refs；保存后不可覆盖 |
| ClaimEvidence | claim_id、draft_revision、文本位置、claim_kind、表述、source_version、artifact_hash、定位、quote/支持类型、限制、access_status |
| AssetRecord | 素材 ID/hash、出处、作者、使用许可或自制依据、审核状态、图注与事实引用；未知使用条件不能标为可发行 |
| AuthorResponse | review_id、issue_id、处理方式、修订版本/位置、理由；不修改原问题单 |
| ReviewReport | brief/draft/evidence/asset/style 版本、reviewer_identity、decision、issues、完成时间；只对绑定快照有效 |
| ReviewIssue | issue_id、severity、category、location、claim_refs、source_refs、reason、resolution_condition、status；解决由新审核确认 |
| ChannelPackage | channel、account_ref、source_draft_ref、rendered_artifact/hash、derivative_review_ref、asset_refs、preview_ref、preflight_version；包含全部对外文本 |
| ReleaseAuthorization | issuer、account/channel、allowed_operations、policy_version、validity、limits、revoked_at；逐篇许可绑定包 hash，自动策略绑定明确条件 |
| ReleaseIntent | story/channel/package_hash、review_ref、authorization_ref、scheduled_at、status、remote_id、idempotency_key、last_error；每渠道独立 |
| DeliveryReceipt | intent_id、attempt_id、provider_status、remote_id、canonical_url、observed_at、evidence_ref；仅提供方已确认事实 |

ClaimEvidence 支持“事实直接依据”“背景”“本文推断”；背景引用不能冒充直接支撑。保留公司自述、预印本、同行评议等来源属性。一般分析允许合理推断，但须明示推断与限制。

精筛统一使用 `ai_quote` 与 `domain_quote` 表达实际 AI 和领域证据；领域涵盖生物、医学或制药，不按历史范围切换字段。

## 4. 公开用例与完成条件

| 用例 | 输入 → 输出 | 校验与副作用 |
|---|---|---|
| list_editorial_queue | cursor、limit、filters → 候选快照/下一游标 | 只读；合并处理状态，不承诺全量语义搜索 |
| commission_story | 候选引用、Brief、expected_revision、幂等键 → story/brief | 校验有效候选、重复选题关系、资源权限；原子预留容量并创建任务书 |
| revise_commission | story、旧版本、ChangeRequest → 新任务书版本 | 不改旧任务书；令依赖旧任务书的未发行授权失效 |
| submit_draft | story、brief_version、完整稿件/证据包 → DraftRevision | 所有必需产物已落档、hash 与权限合法；创建审核任务 |
| submit_review_report | review_task、绑定快照、结论/问题 → ReviewReport | 任务租约有效、独立审阅者、引用合法；pass 需无未解决阻断/必改项 |
| prepare_channel_package | 合格稿件、渠道、账号 → 包/预览/预检 | 只生成产物；实质改写新建衍生版本并审核 |
| authorize_release | 许可或已批准策略、包、计划 → 授权引用 | 仅授权主体/策略服务可调用，发行模型不能自签授权 |
| request_release | 包/审核/授权/排期、幂等键 → ReleaseIntent | 短事务检查当前性、创建意图与 outbox；本操作不等于 published |
| reconcile_delivery | intent、查询结果 → 回执/待处理 | 根据渠道能力核实；不可判定维持 unknown_delivery |
| request_correction | 已发布回执、问题/新版本 → 更正任务 | 新编辑流程；不覆盖原稿、原回执或公开事实 |

CLI/MCP/工作台都是这些用例的协议适配层。示例工具名不是当前命令，不在尚未实现时添加可执行命令说明。

## 5. 分离业务状态与执行状态

选题、稿件、审核、发行分别拥有状态，不用一个全能 status 字段掩盖各阶段失败。

| 聚合 | 状态与合法迁移 |
|---|---|
| 选题 | commissioned → active → completed；commissioned/active → deferred 或 cancelled；deferred 经明确决定重新 active |
| 内容任务 | queued → investigating → drafting → submitted；工作中可 awaiting_change/blocked；恢复经有效任务书与额度校验 |
| 审核任务 | queued → reviewing → pass/changes_requested/blocked；每次复审建新任务/报告，不覆盖终态 |
| 渠道包 | preparing → needs_review/ready；ready 可因源版本/审核/素材变化变 stale |
| 发行意图 | scheduled → dispatching → submitted → published；可 failed/unknown_delivery/cancelled；未知状态先核对再决定 |
| 执行任务 | queued → running → succeeded/failed；可 retry_wait/blocked/cancelled；执行 succeeded 不必等于阅读评审 pass |

选题 completed 表示配置中的交付目标达成（如内部审核包或指定渠道回执），不由模型自行标记。各渠道存在独立发行意图；总体可以 partial，不能用一个 published 掩盖其他渠道失败。

稿件被要求修改时：新建内容修订任务，父稿保持不可变；新稿提交创建新审核任务。旧稿 pass 仍是历史事实，但其许可不能授权发送新稿。任务书重大变更、事实来源撤回或规范强制更新时，当前就绪包转 stale 并停止未发送意图。

审核与发送之间有竞争窗口：dispatch 前再次核验并取得短租约，变更撤销同一聚合版本时只允许一个操作提交。若撤销时远端请求已经发出，记录实际竞争结果并核对；不能承诺已发请求可瞬间撤回。

## 6. 工作单元、产物与可靠投递

SQLite 继续作为单机持久化实现，同一连接下协调跨模块原子操作；SQL 仅在 infrastructure/persistence 实现。网络、模型、文件大对象生成与等待都不在数据库事务内。

1. 产物先写临时文件并计算 hash，原子落到受控路径；短事务保存 manifest 和业务事件。崩溃产生的未引用文件后续清理，不能让数据库引用未落档内容。
2. 立项事务：验证候选/权限与版本、预留容量/预算、保存决定/任务书、追加待执行事件，一起提交。
3. 提交事务：验证任务租约与快照、保存版本/结论、记录事件和下一阶段任务，一起提交。
4. 工作流消费 outbox，幂等调用下阶段用例；消费成功才标投递完成，崩溃可重复投递。
5. 外部发布无法与 SQLite 原子提交：先记录发行意图，再发送，再保存回执。提供方有幂等键则使用；无则依赖查询/人工核对，不承诺 exactly-once。

租约包含 task_id、lease_owner、expires_at、递增 fencing_token；过期由程序回收，新执行令牌使旧进程结果不能提交。所有耗时步骤有超时、有限重试和检查点；取消在工具调用前与提交时检查。

唯一约束建议：候选/选题决策请求幂等键；story_id+draft_version；review_task_id 的最终报告；account_ref+channel+package_hash+release_generation 的发行意图。对同一主题再次报道通过显式 update/followup 关系，而非禁止一切重复立项。

## 7. 失败恢复与运营反馈

| 情况 | 程序动作 | 对角色的反馈 |
|---|---|---|
| 付费模型失败/缺凭证 | 有限重试后保留 blocked，保存输入版本 | 任务未完成，不产出通过或排除结论 |
| 调查取得部分资料 | 持久化已得部分、列出缺口 | 内容可继续非依赖部分，核心缺失申请降级/停止 |
| 任务书并发修改 | 拒绝旧快照提交 | 返回版本冲突与最新引用，重新判断可复用内容 |
| 审核两轮仍有必改项 | 暂停修订队列 | 主编决定补证、缩小范围、延期、取消或独立复议 |
| 渠道包排版失败 | 保留审核合格稿，重试适配 | 不重复调查/写稿 |
| 发布回执丢失 | 标未知并查询，冻结自动重发 | 不把超时当失败后再次发布 |
| 发布后新版本/撤稿 | 关联既有作品，提出复查/更正任务 | 主编定优先级，紧急停止未发包由程序执行 |
| 新来源/系统性漏收 | 提交来源维护请求 | 不直接更改主题规则或所有历史评估 |

记录指标：候选等待、立项率和理由、阶段耗时、用量、审核问题类别、修订轮数、未知投递、渠道回执与更正。先服务运行和质量诊断；点击/阅读反馈如无连接器则明确未知，不伪造为零，不自动让流量改变全局媒体定位。

## 8. 持久化升级与验证

实施按最小业务切片递进迁移，具体 schema 版本由实施时最新迁移确定，不预占版本号。新表由所属模块提供专用仓储；新增索引/外键/唯一约束有临时库验证。迁移前备份，旧库升级后验证原始资料、批次、准则、候选与导出可读。

回滚以停止新写入和恢复兼容备份为主，不倒退 migration 编号。已发生的外部发布无法靠还原数据库撤回；恢复后必须根据远端回执做对账。测试库、回放与生产发行隔离，离线测试禁网/无付费。

验证分四层：纯规则（范围/权限/版本/状态）、临时数据库（并发、事务、outbox、预算）、连接器契约（发送/查询/未知结果）、授权后的真实模型/渠道小样本。正式使用前还需验证常开宿主重启恢复；当前前台调度不等于已部署无人值守服务。
