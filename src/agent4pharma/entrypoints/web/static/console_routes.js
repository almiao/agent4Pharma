/* 唯一正式工作台地址契约；旧书签只转换身份和筛选范围，不渲染旧后台。 */
const roles = { '/intelligence': 'intel', '/editorial': 'editorial', '/content': 'content', '/review': 'review', '/publishing': 'publishing' };
// 导航节点的标识独立于制作任务的角色配置键。
export const productionRoleByNode = { editorial: "editor", content: "writer", review: "reviewer", publishing: "publisher" };
const owners = { editorial:'production', content:'production', review:'human', publishing:'delivery' };
export function consoleUrl(path, metricId = '') {
    const url = new URL(path || '/console', 'http://localhost');
    if (!roles[url.pathname] && !['/', '/console'].includes(url.pathname)) return url.pathname + url.search;
    const query = url.searchParams;
    if (!query.has('stage') && query.has('node')) query.set('stage', query.get('node'));
    const role = roles[url.pathname] || (['intel', ...Object.keys(owners)].includes(query.get('stage')) ? query.get('stage') : '');
    const view = query.get('view');
    if (!role) {
        query.set('view', view === 'observe' ? 'observe' : 'workspace');
        if (query.get('stage') === 'fulltext') query.set('stage', 'selection');
        return '/console?' + query;
    }
    let work = query.get('work') || (view === 'tasks' ? 'tasks' : '');
    if (role === 'intel' && !work) work = metricId === 'intel-history' || query.get('history') === 'all' ? 'history' : {records:'records',triage:'triage',assessments:'tasks',candidates:'candidates'}[view] || 'results';
    if (role === 'editorial' && !work) work = view === 'stories' ? 'stories' : /editorial-(commission|defer|decline|request_evidence)/.test(metricId) ? 'decisions' : 'candidates';
    if (role === 'content' && view === 'artifacts') work = metricId === 'drafts-history' ? 'history' : 'drafts';
    if (role === 'review' && view === 'artifacts') work = metricId === 'reviews-history' ? 'history' : 'reports';
    if (role === 'publishing' && view === 'artifacts') work = metricId === 'packages-invalid' ? 'invalid' : metricId === 'export-receipts' ? 'receipts' : metricId === 'packages-exported' ? 'exported' : 'packages';
    const object = query.get('object') || query.get('task') || query.get('artifact') || query.get('candidate') || query.get('record') || query.get('decision');
    for (const key of ['task','artifact','candidate','record','decision']) query.delete(key);
    query.set('view', 'workspace');
    query.set('work', work || 'tasks');
    if (object) query.set('object', object);
    if (role === 'intel') {
        query.set('stage', work === 'records' ? 'ingest' : work === 'triage' ? 'triage' : 'selection');
        if (!['records','triage','selection'].includes(work)) { query.set('section','records'); query.set('role','intel'); }
    } else {
        query.set('stage', owners[role]);
        if (query.has('batch') && !query.has('scope')) query.set('scope','batch');
        if (work === 'tasks' && object) {
            query.set('stage','production'); query.set('task',object);
            query.delete('object'); query.delete('section'); query.delete('role'); query.delete('work');
        } else {
            query.set('section','records'); query.set('role',role);
        }
    }
    return '/console?' + query;
}

export function workspaceRoute(path) {
    const params = new URL(consoleUrl(path), 'http://localhost').searchParams;
    return {q:params.get('q') || '',phase:params.get('phase') || '',state:params.get('state') || '',page:params.get('page') || '',stage:params.get('stage') || 'overview',story:params.get('story') || '',task:params.get('task') || '',batch:params.get('batch') || '',range:params.get('range') || 'current',scope:params.get('scope') || 'all',run:params.get('run') || '',section:params.get('section') || '',role:params.get('role') || '',work:params.get('work') || ''};
}
