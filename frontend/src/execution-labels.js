/** 展示词典独立于追踪契约；原键和值始终保留，未知字段直接显示原名。 */
export const executionKinds={agent:'Agent',model:'模型调用',tool:'工具调用',mcp:'MCP 调用',workflow:'工作流',event:'事件'};
export const executionStates={started:'运行中',succeeded:'完成',failed:'失败',blocked:'阻断',skipped:'跳过',paused:'已暂停',cancelled:'已停止',incomplete:'未记录结束'};
export const fieldDescriptions={
  context_manifest:'上下文清单',context_delivery:'上下文送达关联',manifest_id:'清单版本',
  receipt_id:'宿主回执',receipt_refs:'本次携带的已关联回执',payload_hash:'公开载荷指纹',
  payload_path:'字段或消息位置',utf8_bytes:'UTF-8字节数',characters:'序列化字符数',
  complete:'采集是否完整',missing_reason:'缺失原因',artifact_id:'材料身份',artifact_hash:'材料版本指纹',
  context_receipt:'上下文回执',state:'观测状态',coordinate_system:'位置坐标体系',

  instruction:'启动指令',bound_input:'绑定输入',context_binding:'绑定任务上下文',system_prompt_prefix:'系统提示词前缀',
  request_header:'请求配置与工具定义',messages:'消息列表',role:'角色',content:'内容',text:'文本',type:'类型',
  model:'模型',provider:'供应商',config:'调用配置',tools:'工具定义',name:'名称',description:'说明',parameters:'参数定义',
  properties:'字段定义',required:'必填字段',arguments:'调用参数',result:'返回结果',isError:'是否工具错误',
  final_response:'公开最终回复',finish_reason:'结束原因',context_complete:'已采集上下文是否完整',
  evidence_id:'资料编号',query:'查询内容',start:'起始字符位置',end:'结束字符位置',limit:'读取字符上限',
  start_page:'起始页码',end_page:'结束页码',after:'继续读取游标',resource:'资源路径',
  title:'标题',summary:'摘要',reason:'理由',decision:'决定',findings:'发现',statement:'发现陈述',
  source_span:'原文位置',limitation:'证据限制',limitations:'限制',open_questions:'未解决问题',
  phase:'阶段',candidate_ids:'候选编号',research_scope:'实际调查范围',reporting:'研究产物',
  evidence:'资料',candidates:'候选',story:'选题',draft:'稿件',reviews:'审核记录',
  max_tokens:'单次输出 Token 上限',max_attempts:'任务尝试上限',timeout_seconds:'超时秒数',
  reasoning_effort:'推理配置',sha256:'内容指纹',content_hash:'内容指纹',fulltext:'是否声明全文',
  truncated:'内容已截断',original_characters:'原始字符数',preview:'内容预览',omitted:'已省略',
  mimeType:'媒体格式',base64_characters:'媒体编码字符数',error_type:'错误类型',
  attempt:'任务尝试',turn:'会话回合',step:'模型步骤',call_id:'调用编号',span_id:'执行单元编号',
  parent_span_id:'父执行单元编号',session_id:'会话编号',trace_id:'追踪编号',agent_id:'Agent 身份',
  framework:'执行框架',server:'MCP 服务',duration_ms:'耗时毫秒',input_scope:'输入采集范围',source:'采集来源',
};
