# 期刊 RSS 发现与定时采集验收

当前解析器为 `rss-v4`：保留明确 `doi` 字段的优先级，同时从条目直属的
`dc:identifier` 逐值识别完整 DOI，支持裸 DOI、`doi:` 和 DOI 解析链接。
ISSN、PII、普通 URL 及简介中的引用不作为论文 DOI；重复 identifier 不因后值覆盖而漏读。
新采集冻结 `rss-v4`，旧版冻结任务继续/重试时拒绝静默更换解析器；已有资料不自动回填或合并。

作者单位提取沿用 `rss-v3` 引入的规则（ADR-048）：可提取条目内明确的 XML/Atom/HTML 作者单位，保存 metadata.affiliations、affiliation_evidence 字段路径和提取版本；未知留空，不补抓详情页。采集快照冻结 feed_parser_version，避免有 rss_profile 的旧运行静默换解析器。

本轮检查既有69个归档Feed、2039条目，没有明确单位字段；另只读实查当前71个启用RSS入口，70个成功、1697条目无明确单位，1个读取失败。可复查 `uv run python scripts/audit_rss_affiliations.py --live`，报告及原件在 data/reports/rss-affiliations；不修改来源配置、历史资料或筛选运行，不调用模型。成功无单位与技术失败分别记录。

2026-10-30 已逐一检查当前目录中的 90 本 watch/candidate 期刊。结果为 **69 本取得可解析且已核对刊名的官方 RSS，20 本访问或解析未通过，1 本未找到可用官方 RSS**。不可用仅指本次运行环境和调查范围，不断言期刊永久没有 RSS。

69 个来源通过定时任务共用的 DailyLiteratureDiscovery/CollectionService/持久化用例真实运行，全部完成采集，批次 `daily-2026-10-29-6e6d8a811ab0a8aa` 归档 **1,498 篇去重资料**。69 个来源均有实际入库的来源记录。首次 RSS 审计共解析 1,728 条；这一数量与按日期窗口采集、资料去重后的数量不同。

## 逐刊结果和原件

- [90 本完整结果表](../data/reports/journal-rss/report.md)：期刊、入口、解析数量、简介数量及实际采集状态。
- [CSV](../data/reports/journal-rss/journal-rss-availability.csv)：包含来源 ID、启停、累计来源记录数。累计来源记录数不等同该批次新增论文数。
- `data/reports/journal-rss/audit.json`：每个候选 URL 的真实 HTTP、解析与刊名核对结果。前两轮快照另外保存，最后一次复核不篡改早期访问证据。
- `data/reports/journal-rss/scheduled-collection.json`：真实逐来源任务与持久化检查点。
- `data/reports/journal-rss/scheduler-smoke.json`：真实 CLI 调度入口重复执行返回 0；已完成的采集未再发起请求。
- `data/reports/journal-rss/*.xml`、对应 JSON：成功 Feed 原件、解析后标题/简介/链接/日期/DOI。`data/` 被忽略，不作为源码提交。

## 不可用清单

| 分组 | 期刊 | 当前结果 |
|---|---|---|
| ACS | ACS Central Science、ACS Synthetic Biology、Analytical Chemistry、Chemical Reviews、JACS Au、Journal of Chemical Information and Modeling、Journal of Chemical Theory and Computation、Journal of Medicinal Chemistry、Journal of the American Chemical Society | RSS/主页访问返回 403 |
| Oxford | Bioinformatics、Bioinformatics Advances、Briefings in Bioinformatics、Nucleic Acids Research | 核对后的 RSS 候选仍返回错误，当前不能自动采集 |
| Oxford | NAR Genomics and Bioinformatics | 主页受阻，未找到可验证的官方 RSS；明确标记不可用，不伪造地址 |
| RSC | Chemical Science、Digital Discovery | 官方 RSS 候选受阻或连接失败 |
| 其他 | Cancer Research、JAMA、PNAS、Genome Research、Computational and Structural Biotechnology Journal | 406/403、连接失败或候选地址不可用；详见逐次证据 |

以上 21 本都登记为 paused，不进入新定时采集；来源说明保留原因与证据位置。它们没有被从关注期刊目录删除，失败也不会写成零篇成功。69 本可用来源登记为 watch，启用 RSS 元数据模式。Nature 复用既有来源 ID，历史 ID 中的 demo 前缀不改变 main 数据集状态。BMJ Digital Health & AI 不在这 90 本目录内，其此前单篇/来源诊断保持原状，没有混入本轮成功数。

## 搜索与核验方法

`config/journal-rss-candidates.tsv` 是逐刊候选清单，包含出版商主页与待核验路径。路径规律、外部目录或搜索命中只是线索。脚本逐条访问候选，失败后从真实主页提取 RSS/Atom 链接。成功必须有可解析条目，并核对频道/出版物名；不会把 HTTP 200 的验证 HTML 当作 Feed。

Springer 的 `Latest Results` 是通用频道名：额外要求条目 DOI 均符合该过滤刊号，并用最多三个样本之一的 Crossref container-title 核对刊名；新条目尚未登记 Crossref 时不以猜测通过。部分 BioMed Central/EMBO 旧入口已迁站，旧地址返回 HTML；已验证的 Springer 过滤 RSS 使用实际新路径。

RSC 官方提供[期刊 RSS 目录](https://pubs.rsc.org/en/ealerts/rssfeed)，AACR 官方提供[RSS 目录](https://aacrjournals.org/pages/rss)，但目录说明不能证明本环境能够读取文件；本轮以项目读取结果为验收依据。

## 定时工具的实际能力

新增 `CollectionPlan.acquisition_mode=feed_metadata`，通过现有来源计划装配 RSS 连接器，直接归档题录和简介，不访问全文详情。单个当前 Feed 最多解析 200 条，不声称完整历史或每日不漏文献；Nature 子刊部分当前 Feed 仅保留 8 条，长期覆盖需持续采集及后续数据库补查。

有时区的发布时间按初始/重叠日期窗口筛选。只有年月日、缺时区或其他不确定时间保留为未知精确时间，并保存原始日期；不会硬填 UTC 零点。简介是实际提供的内容，不自动标成完整摘要或全文。当前批次已完成采集，但尚有待情报精筛的资料，整体业务状态保持 partial。

`feed_metadata` 可按来源配置 `feed_date_policy=current_feed`：每次采集当前 Feed
全部条目（仍最多 200 条），不按批次的初始/重叠日期窗口过滤，原始发表时间保持不变。
Science 整期目录 RSS 使用此策略，避免周刊同一期在非出刊日或迟到后被短日期窗口过滤；
批次日期表示本次采集业务日，不表示条目均在当天发表。重复条目沿用资料身份和来源版本去重。
当前策略冻结在计划版本及批次快照中，历史任务不随当前配置改变。
未配置时仍用 `publication_window`，默认字段不进入序列化，以保留既有计划指纹。
这保证当前响应内的条目不会因发布日期被漏掉，不保证 HTTP 可用、Feed 已移出内容或
独立 First Release 渠道的完整覆盖。Science 的当前目录与提前在线发表渠道须分别核验。

修复 `--collect-only` 的完成条件：调度到期与 CLI 退出按覆盖状态判断，避免采集成功因待模型精筛而每分钟重跑或以失败码退出。普通完整流程仍按原有筛选/简报完成条件执行。历史计划默认序列化与版本指纹保持兼容；新 RSS 模式冻结 rss-v2 与完整计划，版本不匹配拒绝静默续跑。

现有前台调度命令（北京时间 08:00，进程需持续运行）：

```powershell
uv run pharma acquisition schedule --collect-only
```

只执行一次到期检查：

```powershell
uv run pharma acquisition schedule --once --collect-only
```

这两个通用命令会包含所有已启用的数据库采集计划，也包括此前的媒体来源。需要仅跑期刊时，可重复传 `--source <CSV 中启用来源 ID>`；本轮实际调度冒烟限定了 69 个期刊来源。没有新建系统任务或启动常驻调度进程，也没有调用付费模型；“代码支持定时”不等同“主机已配置开机常驻”。

## 复测与验证

```powershell
uv run python scripts/audit_journal_rss.py
uv run python scripts/audit_journal_rss.py --retry-failed
```

上述命令只调查并写报告；`--retry-failed` 复核失败源，已成功源保留先前时间戳。2026-11-07 已移除限定 90 本期刊的一次性登记脚本。来源配置与启停通过正式工作台的“来源与计划”维护，采集另行显式启动；调查结果不自动恢复暂停来源。

离线测试 140 项通过；Ruff 检查、格式检查通过，架构检查 0 错误、11 条审查提示。新增测试覆盖元数据模式实际入库、冻结配置重放、幂等、禁止 HTML 入口混用、默认计划身份兼容，以及只采集任务和待精筛状态的区分。
