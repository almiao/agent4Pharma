# publishing 责任卡

- 拥有：渠道包、预检身份和真实回执。
- 公开入口：release_contracts 的渲染函数、类型与 DeliveryAdapter；应用交接由 workflows 消费，不读取其他模块内部表。
- 依赖：标准库/Pydantic和materials公开文档身份契约；不依赖 SDK、网络、CLI、Web 或持久化实现。
- 专用仓储位于 infrastructure/persistence/sqlite/production_repository.py，工作单元统一提交；不是集中存储门面扩展。
- 新需求放置示例：新增渠道实现放 infrastructure，发布授权和审核不可绕过；本地导出不能记 published。

图文制作新增公开契约 `article_layout`（绑定原图清单、双渠道呈现记录）与确定性 `article_renderer`。跨模块编排由 `workflows/publication_layout.py` 协调；MinerU适配器在基础设施层，图像获取在事务外。发布模块仅使用materials公开DocumentReference，不访问材料内部存储。详见[图文流程](../../../../docs/publishing-layout.md)。

原图目录公开契约位于figure_catalog；查询/看图额度及选择交接由workflows/production_figures协调。目录与PNG缓存、页内带ID缩略图由基础设施生成，发布领域不依赖MinerU或DSH。

人工定稿契约位于 human_approval.py；跨模块有效性由 workflows/human_package_approval.py 校验，人工不替代独立核验。批准记录由发布仓储拥有，所有导出用例复核完整包指纹及当前状态。

article_layout的ArticleEmphasis绑定准确正文指纹和段落原句；article_renderer验证唯一、不重叠位置后只添加strong。强调方案随包保存并参与批准，未填写时保留旧包序列化；不能借呈现层改写正文或扩大事实。

ChannelPackage的edit_root_id/edit_parent_id/edit_pending记录人工图文编辑链与待核验状态；空字段不改变旧批准指纹。图文定点改稿由workflows协调内容与发布模块的同一工作单元，不能直接修改已批准包。

paper_header拥有可选PDF题头素材与读取端口，独立于研究配图；publication_layout协调唯一绑定主文，基础设施负责原始首页裁切。已保存题头在人工改稿时复用，旧包无空字段。裁切失败或过高可省略，不能扩展成任意文件截图工具。

公众号草稿交付契约位于wechat_delivery、准确PNG核对位于wechat_content。发布仓储拥有wechat_deliveries；workflows/wechat_delivery协调短事务防重、批准与未知恢复，infrastructure/web/wechat_drafts实现固定微信API。外部请求只在事务外执行，submitted不等于published，人工确认不存在不覆盖原未知回执。
