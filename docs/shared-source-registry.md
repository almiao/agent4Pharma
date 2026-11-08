# 共享数据源目录：已确认结构与 schema v3

日期：2026-10-29，Asia/Shanghai。用户确认结构，并要求以此前文章和来源入库验证。本轮已实现迁移、demo 导入与查询导出；新来源的自动获取连接器尚未实现。

## 业务定义

管理界面可按期刊、会议、公司/实验室、媒体/博客/账号分组；底层不分别创建四套资料库。

```text
organizations（谁运营/发布）     venues（期刊/会议系列）
           \                    /          |
             sources（来源）         conference_editions（届次）
                    |                       |
             source_endpoints（入口，可关联届次）
                    |
      collection_tasks / source_records → materials → artifacts
```

Source 表示可持续提供资料的网站、栏目或账号；Endpoint 表示具体 API、RSS、网页或 App 访问入口。资料的采集来源、发布机构、作者单位、报道对象分别表达。公司发布论文不意味着必须从公司官网发现；媒体报道公司不意味着公司是该报道的发布者。

## 表与字段

新增三表：

| 表 | 字段 | 规则 |
|---|---|---|
| `organizations` | `id, name, kind, homepage_url, parent_id, watch_status, metadata` | kind：company/university/institute/lab/media/association；parent_id 自引用，可空；不将个人强制归为机构 |
| `conference_editions` | `id, venue_id, year, name, homepage_url, start_date, end_date` | venue_id 外键；同一 venue/year/name 唯一；日期未知留空 |
| `source_endpoints` | `id, source_id, edition_id, url, access_method, connector_key, access_config_id, enabled, metadata` | 来源与 URL 组合唯一；访问方式 api/rss/html/browser/app；配置引用可空；demo 全部 disabled |

扩展现有表：

| 表 | 新增字段 |
|---|---|
| `venues` | `homepage_url`；沿用 journal/conference 等类型，别名和期刊指标不迁移到机构表 |
| `sources` | `name, source_kind, organization_id, venue_id, homepage_url, status, platform, external_id, publisher_name`；保留 descriptor |
| `materials` | `material_type, canonical_url, published_at, publisher_organization_id, venue_id, conference_edition_id, dataset` |
| `collection_tasks` | `endpoint_id`；保留任务范围、游标、成功时间 |

既有访问配置、来源记录、原始文件、任务、发现批次与期刊指标表继续沿用。期刊 IF 按载体和年度保存，允许不存在；不作为资料入库的统一门槛。

`source_kind` 为可扩展字符串，本轮示例包含 news_site、news_section、blog、social_account、journal_site、conference_site、publisher_site、repository_platform、filing_registry 等。不能用一个域名永久判断“一级/二级来源”。资料类型独立于来源类型，支持论文、公告、博客、新闻稿、新闻报道、帖子、代码等；URL 推断的类型标记为 provisional。

`source_records.metadata` 暂存获取状态、分类依据、报道对象名称、引用链接及角色、来源缺口和本地快照位置。引用关系为 cites，角色未核验时为 unassessed。以后若需要频繁按实体/引用关系查询，再迁移为关联表。本轮不增加 events 或人物表。

## Demo 数据

历史初始数据从本地 BioTender 审计结果提取 10 个编辑条目及所引材料，并补 Endpoints 文章和 NeurIPS 2026 会议主页。初始种子与一次性构建脚本现已移除，数据库中的已导入记录保留；下列数量描述初次导入，不代表后续维护后的当前数量。

- 12 个机构、33 个来源、35 个入口、51 份资料、51 条来源记录。
- 关联 Nature 期刊和 NeurIPS 会议，已有别名优先复用，保留已整理的期刊关注与指标。
- 1 条 NeurIPS 2026 届次记录；主页已通过网页工具读取，但并未验证论文列表接口，不伪造一篇会议论文作为样本。
- BioTender 详情和 HTML 已缓存；上游被引用资料多数只定位到链接，不声称获取全文或核实新闻。
- 上游资料的临时标题来自引用锚文本或 URL，并非已核实原文题名，分类标记为 provisional。组织名称/类型为 demo 整理记录，不代表法律实体核验。
- Endpoints 正文访问返回 403；标题明确标注根据 URL 暂拟，发布时间不填；报道对象 BioGeometry 与发布者 Endpoints 分开。
- Chai 原始播客未定位、RFdiffusion4 无外链等缺口保留。
- X 账号以来源表示，个人名称放 publisher_name；尚未获得稳定数字账号 ID，external_id 留空，不将 handle 伪装成稳定 ID。
- 入口的候选主页不是已验证列表，connector_key/access_config_id 暂空；没有因此实现新的浏览器/API 连接器。

### 2026-10-31 治理结果

用户指出来源表不应显示“示例来源 · 非生产接入”，并要求带该标签的来源按项目范围保留或移除。处理如下：

- 界面不再显示 demo 徽标、示例记录数或“示例目录”副标题；`dataset` 元数据只保留给历史资料与导入隔离，不再是界面分类。
- 29 个 demo 来源中保留 9 个 AI × Biology/Pharma 研究或产业来源：BioSpace、Fierce Biotech、Arc Institute、Xaira、Iambic、XtalPi、Phylo、Prima Mente、www.nature.com。它们升为 `main`，保留原 ID 与机构关系，未启用任何采集入口。
- 其余 20 个移除：个人主页/账号、厂商手册与商店、通用新闻稿平台、政府门户、通用会议与评审平台等，均不属于项目主题来源；其未启用入口一并删除，机构元数据退出 demo。
- 当时删除前用 SQLite 在线备份保存到 `data/backups/discovery-pre-catalog-cleanup-*.sqlite3`，检查资料、计划与任务引用，单事务完成并写 `maintenance_audit`。一次性清理脚本已于 2026-11-07 移除，备份与历史审计保留。
- `registry demo` 与 `import-demo` 能力保留给历史核对；本次不改写已归档批次或历史判断。

治理范围见上述历史结果；后续来源变更通过正式工作台维护，不重复执行当时固定的来源清单。

## 运行与查看

```powershell
uv run pharma registry demo
```

该命令查询数据库并重新导出本地报告，不需要种子文件。仅在需要一次性导入外部演示数据时显式使用 registry import-demo --seed C:/imports/demo.json；没有代码库内默认种子。导入仍采用事务和确定性身份，重复执行不新增记录，错误回滚。

2026-10-31 治理后，日常目录以工作台来源页为准；`registry demo` 只剩历史 demo 数据集核对用途，再次导入的示例来源不会再显示“示例来源”标记。

输出：`data/reports/source-demo.json` 和 `data/reports/source-demo-review.md`。后者直接从数据库投影机构、来源、入口、资料及引用关系，可用于逐行评审，而非仅展示种子文件。

资料用 `dataset='demo'` 及 `demo:url:` 身份隔离；目录 ID 使用 demo- 前缀。所有新入口 enabled=0，资料不进入既有 discovery_batch_items。共享真实 venues 身份，不复制现有期刊或覆盖 IF。原来的四来源定时流程仍由现有配置和连接器驱动；历史任务 endpoint_id 留空，本轮没有将它们迁到新入口调度。

## 实际查询示例

```sql
-- 来源、发布机构与访问方式
SELECT s.name, s.source_kind, o.name AS organization,
       e.url, e.access_method, e.enabled
FROM sources s
LEFT JOIN organizations o ON o.id = s.organization_id
LEFT JOIN source_endpoints e ON e.source_id = s.id
WHERE json_extract(s.descriptor, '$.dataset') = 'demo';

-- Endpoints 发布者、报道对象与正文获取状态
SELECT m.title, o.name AS publisher,
       json_extract(r.metadata, '$.reported_subject_names') AS subjects,
       json_extract(r.metadata, '$.access_state') AS access_state
FROM materials m
JOIN source_records r ON r.material_id = m.id
LEFT JOIN organizations o ON o.id = m.publisher_organization_id
WHERE m.dataset = 'demo' AND m.canonical_url LIKE 'https://endpoints.news/%';

-- 被某文章引用的资料：URL 连接，不等同于认定原始来源
SELECT m.title, json_extract(ref.value, '$.url') AS cited_url,
       upstream.material_type, json_extract(ref.value, '$.role') AS role
FROM materials m
JOIN source_records r ON r.material_id = m.id
JOIN json_each(r.metadata, '$.references') ref
LEFT JOIN materials upstream
  ON upstream.canonical_url = json_extract(ref.value, '$.url') AND upstream.dataset = 'demo'
WHERE m.dataset = 'demo' AND m.canonical_url LIKE '%/chai-discovery-interview/';
```

## 迁移与验证

迁移前通过 SQLite 在线备份 API 保存到 `data/backups/discovery-pre-v3-*.sqlite3`。原有按整行字段顺序写入的代码改为显式列名；来源配置更新改用 UPSERT，避免 REPLACE 删除后重建导致引用失效。

验证包括旧数据迁移、原采集流程回归、重复导入、异常回滚、外键完整性，以及发布者/报道对象、访问受限状态的检查。自动采集、原文追溯、账号稳定 ID 与新站点访问适配器属于后续工作，不能从 demo 入库成功推断这些能力已经完成。
