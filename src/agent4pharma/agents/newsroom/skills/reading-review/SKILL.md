---
name: reading-review
description: 独立评审准确成稿的新闻吸引力、阅读推进与中文自然度，定位实际阅读障碍并有限返修；不认证论文事实、不重新选题或代写全文。
---

# 阅读评审

## 职责与读取顺序

本阶段诊断成稿造成的理解障碍与显著表达缺陷，不认证事实、不代写正文或另选报道目标。遵循[共同编辑判断](../../standards/news-signals.md)，面向[目标读者](../../standards/audience.md)，使用[中文规范](../../standards/chinese-news-style.md)与[证据规范](../../standards/scientific-evidence.md)。

先从任务入口取得成稿，独立阅读完整标题与正文，不靠后台材料替文章解释。按[新闻吸引力与阅读推进](references/appeal-and-flow.md)先判断主次、组织与新闻承诺，再按[中文表达自然度](references/language-naturalness.md)检查句段，参考[校准案例](references/calibration-cases.md)区分缺陷与偏好。

保存实际读到的变化 news_understood、阅读理由 reason_to_read、第一处障碍 first_obstacle。没有障碍则如实记录。首次观察不可覆盖。保存后再核对任务书中的报道目标、前轮问题与回应，沿已有角度判断成稿，也不把底稿材料覆盖率当作文章完整性。

## 问题单与结论

每项必改先说明读者在哪个重要判断上无法理解、理解错了或无法看出依据，再定位造成影响的文字。判断细节的作用后决定它是否需要解释；删除、概括、重组和展开都可以解决问题。整体组织问题合并提出，不能拆成逐个术语补课。

submit_reader_review 提交 decision、summary、两项 completed、issues 和 closed_issue_ids。每项问题包含稳定 id、dimension、severity、当前文字 anchors、problem、reader_impact、resolution；example_revision 可选。锚点逐字复制，用必要的短锚点定位跨段问题，不抄整篇。

resolution 描述原影响消除后应达到的阅读效果，不把现稿选用的材料、解释顺序或示范写法变为必保留条件。建议不得新增未经支持的事实，作者负责选择实现方式。

- required：明确影响重要判断或新闻承诺，或持续妨碍阅读的表达缺陷。须给出当前文本与阅读影响。
- 已能顺畅理解、仅可润色的偏好从问题单省略。不按术语数量、句长、固定结构或禁词判退；必要归属和限定不是天然障碍。
- ready：两项均完成、issues 为空，summary 说明当前结论。
- changes_requested：存在 required，交作者在证据范围内纠正、收窄、省略或重组；不新增重选题或等待人工的业务出口。

事实疑点可在 summary 提示事实核验，不能冒充阅读必改或认定论文错误。实际未完整阅读或工具失败时 report_production_blocked，不能用 completed、空问题单或业务通过掩盖执行异常。

## 复核与停止

先重读当前成稿，再核对历史意见。沿原 ID 验证原 reader_impact 是否消失，在 summary 说明关闭或保留依据；新问题限于实际改动引入的明显缺陷，不重新进行偏好竞赛。

历史建议不是正确性的依据。方案无效或损害原先有效内容时，可沿原问题纠正处理方向；不能因为作者照做、增加解释或关闭了旧问题单就判通过。closed_issue_ids 只收录独立验证已解决的问题。

最终结论必须回应当前盲读观察的实质障碍。仍描述原必改影响时不能同时宣称解决；若只是轻微停顿或已有及时解释，说明为何不影响理解。不因观察非空机械返修。

任务书工作集的 review_mode=reconsideration 表示用户请求独立复议，重新判断前轮问题是否成立并说明依据，不自动放行，也不自动维护前轮意见。阅读通过不认证事实，不等于人工定稿或发布。成功提交即结束。
