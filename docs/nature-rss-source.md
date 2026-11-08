# Nature RSS 登记与读取验证

2026-10-30 实测 `https://www.nature.com/nature.rss`：项目 PublicPageFetcher 返回 HTTP 200、`application/rss+xml`，共 75 条。此前一次普通请求得到 Client Challenge，不能据此认定 RSS 持续不可读。浏览器和程序可能因会话、请求头、出口或时间而得到不同响应；尚未通过受控对照定位本次差异的单一触发条件，不宣称已绕过站点验证。

## 数据源状态

此前只有 Nature 官网的 demo 候选 HTML 入口，没有该 RSS。现已通过来源配置公开用例保存 RSS 计划，复用 `demo-source-nature-journal` 身份并提升为 main 数据集，避免重复来源。历史 ID 中的 demo 字样保留，不代表当前数据集仍为 demo。

- 主页：`https://www.nature.com/nature/`。
- RSS：`https://www.nature.com/nature.rss`。
- 状态：paused，`enabled=false`。RSS 当前窗口可读；文章详情提取、日期窗口采集和完整生产链路未验收，不自动开启定时采集。
- 当前条目只有日期，缺少时间和时区。保留 `metadata.date_raw`，`published` 保持未知；计划使用来源顺序，不启用要求精确时间的 newest_first。
- RSS 不代表所有 Nature 子刊，也不代表历史全集。目录可在来源页检查；完整 RSS 计划由管理台来源回归接口及 `pharma acquisition get` 读取，列表首选入口可能仍显示原官网 HTML。

## DSH 读取能力

已有调查 MCP `fetch_page` 经 InvestigationTools 调用 PublicPageFetcher 和 parse_document，后者复用 parse_feed；无需新增另一个同义工具。此次修复 RSS 1.0 的 `content:encoded` 简介缺失，去掉该字段中的 HTML/脚本，保留多位 dc:creator 作者和 prism:doi。原有 description/summary/content 字段优先级不变。新结果记录 `feed_parser_version=rss-v2`；不修改历史采集原件或评估。

实际响应复测：75 条均取得简介；DSH 工具共用的 parse_document 产生 75 条 feed_entry 并带可读简介。简介不是论文完整摘要或全文。Nature 当前流同时有研究、新闻等内容，仍需后续相关性筛选。

本轮验证了真实 HTTP 读取和 DSH 共用解析路径，没有启动付费模型会话，没有声称 Agent 已完成语义筛选。DSH 是工具调用运行底座，其现有普通 HTTP 工具不会继承人工浏览器会话；若以后稳定出现验证门，应明确返回阻断，再单独验证浏览器适配器，而不是把验证页当成正常资料。

## 验证与产物

- 新增离线测试覆盖 RDF 命名空间、encoded 简介、脚本剔除、作者/DOI、缺时区不造时间，以及既有 description 优先行为。
- 真实原件与摘要：`data/reports/nature-rss/feed.xml`、`entries.json`、`probe.json`。产物目录被忽略，不含凭证。
- 注册和探测只通过现有来源公开契约，不直接写跨模块表。没有新增浏览器权限、模型权限或系统定时任务。
- 实际验证：pytest 138 项通过；Ruff 检查、格式检查通过；架构检查 0 错误、11 条审查提示。parse_feed 保留单函数解析职责，69 行触发提示，不为压低行数拆散同一条记录的映射。
