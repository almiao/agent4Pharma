# 外部设施实现

实现来源、模型运行和存储等外部边界；业务规则依赖契约而非这些具体类。

- connectors：按来源协议解析，transport 处理限流/超时/重试/原始响应留档。
- llm：DSH SDK 生命周期及权限 patch，SDK 仅在实际 run 时初始化。
- llm/dependency_probe：运行台只读健康检查及脱敏解析任务列表；20秒缓存和有限超时，不恢复服务、不调用付费模型。公开响应契约归 runtime/dependency_status，composition 装配。
- web/daily_browser_connection：由运行台显式关闭的常驻 stdio 所有者，惰性建线程，串行复用已授权页面；域端口只公开连接／状态／关闭，不暴露脚本。断线先恢复连接，打开出版商前才占下载次数；stderr 只在内存中过滤宿主连接事件。
- web/daily_browser_*：通过锁定版本的官方 Playwright MCP 扩展复用日常 Chrome；宿主固定论文步骤，底层通用工具不暴露给 DSH，不读取或导出浏览器凭证。浏览器扩展仍需用户安装并确认连接，旧账号暂停和额度继续生效。
- persistence/sqlite：具名仓储与事务工作单元，全部 SQL 集中在此。
- persistence/sqlite/discovery_query_cache：HTTP服务持有的有界批次投影缓存，以只读SQLite连接的data_version监测提交，以文件身份监测数据库替换；变化即失效、错误不缓存、返回独立对象，关闭服务时释放。生产查询使用query_only的快照工作单元，不与写操作争用写租约。
- SqliteApplicationStorage 仍聚合当前试点存储操作；新增业务使用对应模块契约，不能复制全能存储门面。
- 新增设施前确认协议或资源边界；不能将业务成功条件、候选价值或审核结论交给适配器决定。
