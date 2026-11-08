# 来源目录

`rss_profile` 保存逐源真实 RSS 字段角色、测量、提取映射和原件 hash；信号与版本交资料模块归档，不因 description 非空就认定有摘要。

管理机构、账号、期刊/会议、别名、指标和访问入口；目录信息不等于已经实现获取能力。

- 公开契约：directory_repository.SourceDirectoryRepository；身份规则：venue_identity。
- 展示：directory_reports，不直接访问数据库；具体 SQL 在 infrastructure/persistence/sqlite。
- 新期刊别名或目录字段在本模块及对应仓储扩展；实际 RSS/网页获取交连接器与 collection。
- 目录可以关联资料，但不能因来源可信度而代替内容审核；缺失指标不能写零。
- 不再保留只转发仓储方法的 service.py；有独立业务校验时再建立具名用例。
- collection_plans 提供通用采集计划、字段和发现入口的具名契约；当前定义及不可变历史归来源模块，由独立 CollectionPlanRepository 保存。站点差异只配置到数据库，不新增站点代码或代码库内实例配置。计划更新通过 expected_hash 校验并发版本。
- 期刊可显式配置 `acquisition_mode=feed_metadata` 与 `source_kind=journal_site`；只读取单个 RSS 的有界题录，不要求详情成功。默认详情计划序列化保持兼容。逐刊候选清单只是审计输入，实际启停和计划仍以数据库为准。
- 整期 RSS 可配置 `feed_date_policy=current_feed`，按当前响应全量接收，保留实际发表时间并复用去重；默认 `publication_window` 保留日期窗口行为及旧计划指纹。日期策略随计划和任务冻结，不按刊名在代码中分支。

- `source_categories` 提供展示分类；公开 `SourceCategory` 类型供运行查询契约引用，分类不参与主题判别或运行状态。
- `management.SourceCollectionAvailability` 保存来源当前采集能力：入口已验证、当前无法采集、待接入。逐次检查保留 URL、HTTP 状态、解析/身份核验结果、时间及响应哈希；不将一次访问限制视为永久不可用。仓储乐观锁拒绝过时复核，历史审计保留旧结论；关注/暂停、入口启用及历史采集任务互不代替。
- 目录优先展示当前采集计划的首入口，旧入口仍留存。修正 Feed 地址更新计划版本；RSS 请求明确接受 XML，不继承浏览器登录态，也不放宽公开地址校验。
- `management.SourceDirectoryFilters` 提供采集可用性、实际来源类型、关注状态与分页偏移契约；仓储先组合关键词与筛选再分页，`source_kinds` 读取完整目录实际类型。未复核不等于不可用，关注状态不代表访问能力。
