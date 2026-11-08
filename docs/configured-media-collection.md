# 数据库配置驱动的媒体采集

日期：2026-10-29（Asia/Shanghai）。四站的实际采集规则只保存在数据库 source_collection_plans 中；代码库不维护站点配置文件。所有媒体共用 ConfiguredWebConnector，没有按媒体名称或域名分支的代码。

## 配置和执行

```powershell
# 直接查询数据库中的计划及当前版本
uv run pharma acquisition list
uv run pharma acquisition get <source_id>
# 从标准输入提交单个计划 JSON，直接校验并写数据库，不联网
# 新建可不提供 expected-hash；更新必须使用 get 返回的 content_hash
$json | uv run pharma acquisition save --expected-hash <content_hash>
uv run pharma acquisition disable <source_id> --expected-hash <content_hash>
uv run pharma acquisition enable <source_id> --expected-hash <content_hash>
# 只采集和粗筛；情报判断保持待处理
uv run pharma acquisition run --collect-only
# 前台常驻：北京时间 08:00 日常发现及未完成任务重试
uv run pharma acquisition schedule --collect-only
# 按指定批次的历史冻结配置和原队列续跑
uv run pharma acquisition resume <batch_id> --collect-only
```

普通 pharma run/schedule 同样装配数据库启用计划，并继续原有文献平台配置；acquisition run/schedule 只执行通用媒体。选用一个调度宿主即可，不需两个进程同时采集。可重复传入 `--source <数据库来源ID>` 限定媒体；已有 demo 来源 ID 保留，先用 list 查看。数据库是唯一配置源。save/get 使用请求/响应 JSON 作为协议载体，不读取站点文件；不存在从代码库自动导入或覆盖数据库的步骤。启停同样直接写数据库，修改后新运行生效。

不传 --collect-only 会进入既有情报筛选，需要凭证和预算。退出码 2 表示待处理判断或部分失败，结合报告 coverage_status 和逐来源状态判断。采集成功但尚未精筛也不能称整批完成。

此功能不安装 Windows 系统计划任务或后台服务，宿主、代理需持续在线。前台进程每轮重新装配计划，启停和规则更新影响新运行。若现有工作台锁住 pharma.exe，已安装依赖后可用 `uv run --no-sync python -m agent4pharma ...` 执行同样命令；停止旧宿主后再正常 uv sync，不强制结束现有进程。

## 通用规则与边界

- 一个来源配置名称、主页、启停、允许域名、多发现入口和详情规则。RSS 共用解析器；HTML 使用 link_selector 和 next_selector；max_pages/max_links 限定当前扫描窗口。allow_empty 默认关闭，零匹配是规则失配，不能静默报告成功。
- `feed_metadata` 的 `feed_date_policy` 默认 `publication_window`，按采集日期窗口过滤；显式
  `current_feed` 则每次接收当前 Feed 全部条目，适合整期周刊或迟到更新。发表时间仍用来源原值，
  每日批次仅表示采集日；重复条目复用现有去重。该选项不可用于详情采集模式，且参与计划版本冻结。
- RSS 支持 Dublin Core `dc:date`、RSS pubDate、Atom published/updated，保留原字段和值，只将带时区时间点转换为 UTC。`order=newest_first` 在 max_links 截断之前排序，任一条目缺可排序时间则明确失败，不能把 Feed 第一条冒充最新；默认 `order=source` 沿用来源顺序并省略默认新字段，保持旧计划指纹。
- 发现游标记录每页 available、selected、order 与首条日期；详情失败后的补抓队列保留这些依据。原始 RSS 的时间可能是发表或更新语义，不能把排序时间一概认证为文章发表时间。
- 详情标题、正文、作者、规范 URL、外部 ID、发表/修改日期均使用有序 CSS selectors 和可选 attribute，取首个非空值。remove_selectors 排除噪声；paywall_selectors/paywall_text 识别预览。缺失身份/正文为失败，超长正文标 truncated，不能仅凭 HTTP 200 认定全文。
- 默认身份是规范 URL；可配置页面稳定 ID，将 Feed 别名与详情地址合并。只删除 utm_* 和 fragment，不随意删除业务查询参数。正文实际引用链接单独留存；报道引用的 DOI 不作为报道自身 DOI，不合并为论文。
- 仅带明确时区的时间点转换为 UTC；仅年月日保留 day 精度及原文，不补造零点/时区。无时区或无法解析的值保持未知。Feed 频道 lastBuildDate 不作为逐篇日期。
- 配置只表达数据规则，不支持 Python、SQL、eval 或站点回调。未知键和无效 CSS 在写入前拒绝；代理来自进程环境/系统，不写入来源配置。
- 当前支持公开 RSS 和静态 HTML，不包含 JavaScript 渲染、登录态浏览器或任意 API 协议。后续应按需要增加可复用能力，不能通过配置伪装为已实现。HTTP 403、付费预览、规则失配分别呈现。

## 持久化与恢复

来源模块拥有 source_collection_plans 当前计划及 source_collection_plan_versions 不可变历史，由独立仓储管理，不扩大 SqliteApplicationStorage 门面。显式保存按 enabled 设置 watch/paused，并按同主页复用来源 ID、保留机构关系。更新在同一事务校验 expected_hash，过期或缺失版本拒绝覆盖；当前配置与不可变历史一起保存。每个发现入口仍登记 source_endpoints，一个来源计划协调多入口，不再只取第一个入口。旧 demo 资料、历史批次不改写，新资料使用 production 数据集。

计划定义及连接器版本参与批次指纹并冻结。默认执行当前数据库规则，resume 执行旧批次规则；程序连接器版本不匹配时明确拒绝，不静默重解释历史。

2026-10-30：通用提取器版本前进到 configured-web-v2（RSS 日期/最新排序与发现观测）。旧 v1 批次保持原快照；未完成 v1 队列需使用对应旧实现，或显式创建新版运行，不能用新版静默续跑。

初页提交发现 URL 队列和原始响应，后续逐篇原子提交资料、来源版本、批次成员、产物及游标。失败文章进入检查点的补抓队列，其余文章继续；一轮结束仍失败或耗尽页预算则保持 partial。续跑只处理未完成队列。网络、等待和模型均不在数据库事务内。

资料继续使用 materials/source_records/artifacts。同一外部身份对应一份资料，文本、元数据或规则版本变化追加来源版本；抓取时间和留档时间不参与版本指纹。原始 HTTP 成败响应留档，解析失败仍可追溯。正文、付费导读和截断内容分别为 fulltext/preview/truncated 产物，情报输入明确标注访问范围。

媒体使用每日批次标签，但接收当前窗口发现的新文和更新，不硬筛为昨日发表。当前 RSS 不是过去业务日的完整快照；补跑保证已保存队列可恢复，不保证 Feed 已移出内容的历史召回。

## 四站真实复测

| 来源 | 数据配置 | 本机真实结果 |
|---|---|---|
| BioPharmaTrend | RSS + Latest Insights；页面 ID；详情正文/日期/付费提示 | 修正 CSS 配置后两个入口合并为 12 篇；正文与付费导读区分 |
| Decoding Bio | Substack RSS + 官网 Newsletter；详情正文及 time[datetime] | 13 篇；RSS 比官网静态列表更新 |
| BioTender | 首页焦点及 Latest Stories；article.prose | 4 篇；详情日期只有天精度，原值保留 |
| Endpoints News AI 栏目 | HTML 列表及详情 CSS | 入口 HTTP 403；失败响应留档，提取规则尚未通过真实内容验证 |

29 篇是配置窗口的实测结果，不是全站历史，也不代表都符合 Agent/LLM 制药主题。Endpoints 不记为零结果成功，使采集覆盖保持 partial。本次没有付费模型调用。

离线回归覆盖多入口、别名去重、正文/预览、引用、版本幂等、分页、字段缺失、日期精度、失败恢复、事务回滚、历史配置续跑和 v5→v6 备份恢复。schema v6 升级前备份至 data/backups/discovery-pre-v6-*.sqlite3；回退时停写，在独立目录恢复备份验证。
