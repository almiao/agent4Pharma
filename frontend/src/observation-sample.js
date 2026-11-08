/** 有明确任务、调用与产物关系的虚构采编样例；沿用真实交接语义，不冒充生产日志。 */
export const story = { id: 'demo-cell-01', title: '细胞轨迹模型能否跨组织泛化？', batch: '2026-11-02 · 样例批次', model: '示例模型', skill: 'newsroom-demo-v2' };
export const artifacts = {
  paper: { title: '论文证据 · paper-v1', type: '材料', producer: null, body: '【虚构论文摘录】\n研究在两个预设单细胞数据集上评估轨迹重建。实验结果支持模型在这两个数据集内恢复状态变化。\n\n限制：尚未开展跨组织验证；两个数据集的表现不能证明普遍泛化能力。', quote: '尚未开展跨组织验证。' },
  brief: { title: '选题任务书 · brief-v1', type: '任务书', producer: 'pitch', event: 'pitch-submit', body: '报道问题：这项方法帮助我们理解了什么，又有哪些边界？\n\n读者利益：区分数据集内的轨迹重建与跨组织泛化。\n\n必答问题：方法输入是什么？实验支持什么？还有什么未验证？\n\n验收：以具体结果开篇；准确标注验证范围；不得扩大原文结论。' },
  draft1: { title: '稿件 v1 · 历史版本', type: '稿件', producer: 'write1', event: 'w1-submit-ok', body: '一种模型尝试从单细胞观测中重建细胞状态变化。研究在两个预设数据集上完成评估。\n\n## 从静态观测理解变化\n模型把不同细胞的观测组织成轨迹，帮助研究者描述状态变化。\n\n## 下一步走向跨组织应用\n这些结果表明，模型已具备跨组织泛化能力，有望用于更广泛的组织研究。', quote: '模型已具备跨组织泛化能力' },
  review1: { title: '核验报告 v1 · 要求修改', type: '核验报告', producer: 'review1', event: 'r1-submit', body: '结论：要求修改（changes_requested）\n\n问题 R-01：稿件将两个数据集内的评测扩大为跨组织泛化。\n原稿：“模型已具备跨组织泛化能力”。\n原文：“尚未开展跨组织验证”。\n\n解决条件：删除已验证泛化的表述，明确当前证据范围。', quote: '模型已具备跨组织泛化能力' },
  draft2: { title: '稿件 v2 · 当前版本', type: '稿件', producer: 'write2', event: 'w2-submit', body: '一种模型尝试从单细胞观测中重建细胞状态变化。研究在两个预设数据集上完成评估。\n\n## 从静态观测理解变化\n模型把不同细胞的观测组织成轨迹，帮助研究者描述状态变化。\n\n## 跨组织表现仍待验证\n现有结果支持模型在两个预设数据集内重建轨迹。跨组织泛化尚未验证，不能据此推断更广泛的适用性。', quote: '跨组织泛化尚未验证' },
  review2: { title: '核验报告 v2 · 通过', type: '核验报告', producer: 'review2', event: 'r2-submit', body: '结论：通过（pass）\n\n绑定稿件：draft-v2。\n问题 R-01：已解决。稿件明确跨组织泛化尚未验证。\n核验结论仅适用于此稿件版本，不能用于历史稿件 v1。' },
};
const tool = (id, time, title, name, input, output, extra = {}) => ({ id, time, title, kind: 'tool', name, input, output, status: 'succeeded', ...extra });
const message = (id, time, text, extra = {}) => ({ id, time, kind: 'model', title: 'Agent 公开响应', text, status: 'succeeded', ...extra });
export const tasks = [
  { id: 'pitch', label: '主编立项', role: '主编 Agent', phase: 'topic-selection', parent: null, start: '10:20:00', end: '10:20:24', outcome: '已立项', status: 'succeeded', inputs: ['paper'], outputs: ['brief'], events: [
    tool('pitch-read','10:20:03','读取绑定证据','read_material',{material_id:'paper-v1'}, {text:artifacts.paper.body}, {outputArtifact:'paper'}),
    message('pitch-model','10:20:12','选择“评测范围与泛化边界”作为报道角度，要求写作明确未验证的部分。'),
    tool('pitch-submit','10:20:24','提交任务书并交接写作','submit_editorial',{decision:'commission',brief:artifacts.brief.body},{brief_id:'brief-v1',next_task:'write1'}, {outputArtifact:'brief',next:'write1'}),
  ] },
  { id: 'write1', label: '正文写作', role: '写作 Agent', phase: 'draft-writing', parent: 'pitch', start: '10:21:00', end: '10:23:08', outcome: '生成稿件 v1', status: 'succeeded', inputs:['brief','paper'], outputs:['draft1'], events:[
    tool('w1-skill','10:21:02','读取写作技能','read_skill_resource',{path:'draft-writing/SKILL.md',version:'newsroom-demo-v2'}, {text:'以具体结果开篇，说明验证范围；每条事实主张绑定已读取证据。提交前完成结构与事实自检。'}),
    tool('w1-read','10:21:08','读取论文结果与限制','read_material',{material_id:'paper-v1',section:'results_and_limitations'},{text:artifacts.paper.body},{outputArtifact:'paper'}),
    message('w1-model','10:21:35','已读任务书与论文结果。将围绕轨迹重建结果组织正文，并提交带来源的稿件。',{input:'任务书 brief-v1 + 论文结果与限制 + 已读取的写作技能',tokens:'输入 2,410 / 输出 682'}),
    tool('w1-submit-rejected','10:22:10','首次提交被宿主拒绝','submit_draft',{title:story.title,body:artifacts.draft1.body,claims:[{text:'模型重建细胞状态轨迹',evidence_ids:[]}]},{accepted:false,error:'主张缺少证据引用',draft_created:false}, {status:'rejected',parentCall:'w1-model'}),
    message('w1-correction','10:22:18','提交未通过引用校验。补充已读取材料的证据身份后再次提交；当前会话继续执行。',{input:'工具返回：accepted=false；主张缺少证据引用',tokens:'输入 3,124 / 输出 710'}),
    tool('w1-submit-ok','10:23:08','补充引用后提交成功','submit_draft',{title:story.title,body:artifacts.draft1.body,claims:[{text:'模型重建细胞状态轨迹',evidence_ids:['paper-v1:results']}]},{accepted:true,draft_id:'draft-v1',next_task:'review1'},{outputArtifact:'draft1',next:'review1',parentCall:'w1-correction'}),
  ] },
  { id:'review1',label:'事实核验',role:'核验 Agent',phase:'fact-check',parent:'write1',start:'10:23:12',end:'10:24:02',outcome:'要求修改',status:'changes',inputs:['draft1','paper'],outputs:['review1'],events:[
    tool('r1-read','10:23:15','读取稿件与原文','read_review_context',{draft_id:'draft-v1',material_id:'paper-v1'},{draft:artifacts.draft1.body,source:artifacts.paper.body}),
    message('r1-model','10:23:45','稿件称“已具备跨组织泛化能力”，但原文明确尚未验证。引用存在不等于结论成立，需要退回修改。',{input:'draft-v1 + paper-v1',tokens:'输入 2,811 / 输出 326'}),
    tool('r1-submit','10:24:02','提交必改问题 R-01','submit_review',{draft_id:'draft-v1',decision:'changes_requested',issue:artifacts.review1.body},{report_id:'review-v1',next_task:'write2'},{outputArtifact:'review1',next:'write2'}),
  ] },
  { id:'write2',label:'正文修订',role:'写作 Agent',phase:'draft-writing',parent:'review1',start:'10:24:08',end:'10:25:16',outcome:'生成稿件 v2',status:'succeeded',inputs:['draft1','review1','paper'],outputs:['draft2'],events:[
    tool('w2-read','10:24:11','读取返工问题与原稿','read_revision_context',{draft_id:'draft-v1',report_id:'review-v1'},{problem:'R-01：跨组织泛化表述超出证据',original:'模型已具备跨组织泛化能力',source:'尚未开展跨组织验证。'}),
    message('w2-model','10:24:42','将结论收束为两个数据集内的轨迹重建，并保留跨组织验证缺失这一限制。其余事实段落沿用。',{input:'稿件 v1 + 核验报告 v1 + 绑定原文',tokens:'输入 2,634 / 输出 584'}),
    tool('w2-submit','10:25:16','提交修订稿 v2','submit_draft',{body:artifacts.draft2.body,resolves:['R-01']},{accepted:true,draft_id:'draft-v2',previous:'draft-v1',next_task:'review2'},{outputArtifact:'draft2',next:'review2',diff:{before:'模型已具备跨组织泛化能力',after:'跨组织泛化尚未验证，不能据此推断更广泛的适用性。'}}),
  ] },
  { id:'review2',label:'修订核验',role:'核验 Agent',phase:'fact-check',parent:'write2',start:'10:25:20',end:'10:26:00',outcome:'稿件 v2 通过',status:'succeeded',inputs:['draft2','review1','paper'],outputs:['review2'],events:[
    tool('r2-read','10:25:23','核对修订与证据','read_review_context',{draft_id:'draft-v2',issue_id:'R-01'},{revised:'跨组织泛化尚未验证',source:'尚未开展跨组织验证。',matches:true}),
    message('r2-model','10:25:48','R-01 已解决。修改后的表述与证据范围一致，可以生成排版待审稿，交由人工审稿定稿。'),
    tool('r2-submit','10:26:00','提交通过结论','submit_review',{draft_id:'draft-v2',decision:'pass',resolved:['R-01']},{report_id:'review-v2',next_task:'layout'},{outputArtifact:'review2',next:'layout'}),
  ] },
  { id:'layout',label:'排版待审稿',role:'排版 Agent',phase:'layout',parent:'review2',start:'10:26:04',end:'10:26:18',outcome:'发布包 v2 已生成',status:'succeeded',inputs:['draft2','review2'],outputs:['release2'],events:[
    tool('layout-submit','10:26:18','生成完整待审发布包','prepare_release',{draft_id:'draft-v2',template:'article-layout-v1',media:[]},{package_id:'release-v2',references:['paper-v1'],next_task:'human'},{outputArtifact:'release2',next:'human'}),
  ] },
  { id:'human',label:'人工审稿与定稿',role:'人工编辑',phase:'human-review',parent:'layout',start:null,end:null,outcome:'等待人工审稿',status:'queued',inputs:['release2','review2','paper'],outputs:[],events:[] },
  { id:'publish',label:'发布',role:'发布服务',phase:'layout-production',parent:'human',start:null,end:null,outcome:'等待人工批准',status:'queued',inputs:[],outputs:[],events:[] },
];
artifacts.release2 = { title: '待审发布包 · release-v2', type: '发布包', producer: 'layout', event: 'layout-submit', body: `标题：${story.title}\n排版：article-layout-v1\n配图与图注：无（本篇纯文字样例）\n引用：paper-v1\n\n${artifacts.draft2.body}` };
export function taskById(id) { return tasks.find(t=>t.id===id); }
export const edges = tasks.filter(t=>t.parent).map(t=>({from:t.parent,to:t.id,artifacts:t.inputs.filter(a=>artifacts[a].producer===t.parent),reason:t.id==='write2'?'必改返工':t.id==='publish'?'人工批准后直接发布':t.id==='human'?'完整发布包交人工审稿':t.id==='layout'?'核验通过后生成待审稿':'结构化产物交接'}));
