/** 批次筛选 fixture；集合、排名、整轮复核与单篇交接均使用显式身份。 */
const titles = ['细胞轨迹模型能否跨组织泛化？','蛋白质生成模型的功能约束','自动实验平台的多模态规划','RNA 调控模型的新评测','生物基础模型的数据质量研究','抗体设计的实验验证','组学模型的跨队列迁移','科研 Agent 的检索评测','医学多模态模型的可靠性','基因组模型的长序列建模','合成生物学中的生成式设计','蛋白质结构基础模型评测','传统回归方法分析生存率','单细胞基础模型的训练数据'];
export const batch = {id:'batch-demo-1102',date:'2026-11-02',sourceRun:'collect-demo-01',policy:'paper-news-v2-demo',screenRun:'commercial-demo-01',title:'AI × Biology/Pharma · 日常采编'};
export const papers = titles.map((title,i)=>({id:`paper-${String(i+1).padStart(2,'0')}`,title,source:i%2?'示例 RSS B':'示例 RSS A',journal:i<6?'样例高价值期刊':'样例专业期刊',journalTier:i<6?'优先档':'常规档',institution:i===0?'样例研究机构（来源单位字段）':'未知',text:i===0?'研究在两个预设单细胞数据集上评估轨迹重建；跨组织泛化尚未验证。':i===12?'研究使用传统回归模型分析临床队列，不包含深度学习或基础模型。':`【虚构来源文本】${title}，报告实验范围、评测结果与尚未验证的边界。`,info:i===3?'title_only':'abstract',coarse:i===12?'excluded':i===13?'failed':'passed',probability:i===12?0.08:i===13?null:0.91-i*.012,score:i<12?8.4-i*.41:null,rank:i<12?i+1:null,selected:i<5,pdf:i<2?'associated':i<5?'waiting':'not_requested',final:i===0?'recommend':i===1?'exclude':null,story:i===0?'demo-cell-01':null}));
export const scopes = [
 {id:'collected',title:'来源采集',unit:'篇资料',subtitle:'原始输入与来源记录'},
 {id:'coarse',title:'初筛',unit:'篇待判断',subtitle:'主题门槛 · 技术失败独立保留'},
 {id:'rank',title:'本地摘要排序',unit:'篇参与排序',subtitle:'期刊档位优先，同档按独立排序值'},
 {id:'review',title:'商业整体复核',unit:'篇冻结输入',subtitle:'一轮比较，最多选择 5 篇'},
 {id:'fulltext',title:'全文准备与终判',unit:'篇入选',subtitle:'人工下载 → 全文证据 → 正式终判'},
 {id:'production',title:'采编制作',unit:'篇制作中',subtitle:'选题 → 写作与核验 → 排版'},
 {id:'human',title:'人工审稿与定稿',unit:'篇审稿中',subtitle:'最终预览 · 精修 · 退回修改 · 确认'},
 {id:'publication',title:'发布',unit:'篇待发布',subtitle:'已批准版本 · 发布结果 · 失败重试'},
];
export function collection(stage) { return papers.filter(p=>stage==='rank'?p.coarse==='passed':stage==='review'?p.rank && p.rank<=10:stage==='fulltext'?p.selected:stage==='production'?p.final==='recommend':true); }
export function outcome(p,stage) { if(stage==='coarse') return {passed:'初筛通过',excluded:'主题排除',failed:'技术失败'}[p.coarse]; if(stage==='rank') return p.rank<=10?'进入前 10':'未进前 10'; if(stage==='review') return p.selected?'摘要入选':'本轮未入选'; if(stage==='fulltext') return p.pdf==='waiting'?'待人工下载':p.final==='recommend'?'正式推荐':'全文终判排除'; if(stage==='production') return '已进入制作';return '已采集'; }
export function decisionReason(p,stage) { if(stage==='coarse') return p.coarse==='failed'?'本地模型请求超时，未得到主题结论；需显式重试。':p.coarse==='excluded'?'来源文本仅使用传统回归，不满足 AI × Biology/Pharma 主题门槛。':'来源文本涉及深度学习或基础模型与生物问题，进入后续摘要判断。'; if(stage==='rank') return p.rank<=10?'在期刊档位内按独立新闻价值排序值收敛，进入本轮冻结前 10。':'主题通过，但本轮同档排序未进入前 10；不调用商业复核。'; if(stage==='review') return p.selected?'本轮比较认为有明确方法增量或评测价值，进入全文清单；尚非正式推荐。':'与同轮候选比较，当前来源文本所支持的新闻增量有限。';if(stage==='fulltext') return p.pdf==='waiting'?'等待人工下载并确认关联，尚未执行全文终判。':p.final==='recommend'?'已读全文证据支持主题与推荐价值，生成正式候选。':'全文终判认为推荐价值不足。本次结束，保留原摘要入选事实。';return '来源记录、论文身份与批次归属已冻结。'; }
export function reviewInput() { return collection('review').map(p=>({paper_id:p.id,title:p.title,text:p.info==='title_only'?null:p.text,information_status:p.info,journal_tier:p.journalTier,institution:p.institution,local_rank:p.rank})); }
export function reviewOutput() { return {run_id:batch.screenRun,selected_ids:papers.filter(p=>p.selected).map(p=>p.id),decisions:collection('review').map(p=>({paper_id:p.id,selected:p.selected,reason:decisionReason(p,'review')}))}; }
