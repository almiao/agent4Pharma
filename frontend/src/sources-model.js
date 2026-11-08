/** 来源目录地址参数和安全外链；不在浏览器过滤服务端分页数据。 */
export const sourceKinds = {journal_site:'期刊网站',journal:'期刊',preprint_server:'预印本平台',repository_platform:'资料平台',news_site:'新闻网站',news_media:'新闻媒体',news_section:'新闻栏目',organization:'机构',company:'企业',publisher:'出版机构',database:'数据库',community:'社区',publisher_site:'出版机构网站',announcement:'公告',blog:'博客',website:'网站'};
export function readSourceFilters(search) {
  const params = new URLSearchParams(search), offset = Number(params.get('offset') || 0);
  return {q:params.get('q') || '',availability:params.get('availability') || '',kind:params.get('kind') || '',status:params.get('status') || '',offset:Number.isSafeInteger(offset) && offset >= 0 ? offset : 0};
}
export function sourceQuery(filters) {
  return new URLSearchParams(Object.entries(filters).filter(([,value])=>value !== '' && value !== 0));
}
export function safeHttpUrl(value) {
  try { return ['http:','https:'].includes(new URL(value).protocol); } catch { return false; }
}
export function policyDefinition(baseline, draft) {
  const definition = {...baseline,...draft};
  for(const key of ['agent','model','drug','review']) definition[key + '_terms'] = draft[key + '_terms'].split('\n').map(value=>value.trim()).filter(Boolean);
  return definition;
}
