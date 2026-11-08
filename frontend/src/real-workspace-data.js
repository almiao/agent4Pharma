/** 按工作范围读取正式数据；制作页不等待采集资料或筛选历史。 */
export async function loadWorkspaceData(api, { batch = '', intelligence = false, operations = false, recordBatch = '', selection = false, run = '', node = '' } = {}) {
  const errors = [];
  async function read(key, path) {
    try { return [key, await api(path)]; }
    catch (error) { errors.push(`${key}：${error.message}`); return [key, null]; }
  }
  if (node === 'rank' && !operations) {
    const params = new URLSearchParams({view:'rank',...(batch ? {batch} : {}),...(run ? {run} : {})});
    const [[,page],[,production]] = await Promise.all([
      read('preselection','/api/rss-triage?'+params),
      read('production','/api/production/paper-status'),
    ]);
    return {data:{preselection:page,production,discovery:page ? {selected:page.selected,batches:page.batches.map(item=>item.batch_id)} : null},errors};
  }
  if (selection) {
    const params = new URLSearchParams();
    if (batch) params.set('batch', batch);
    if (run) params.set('run', run);
    const [[, page],[, production]] = await Promise.all([
      read('preselection', '/api/paper-selections' + (params.size ? '?' + params : '')),
      read('production','/api/production/paper-status'),
    ]);
    return { data:{preselection:page,production,discovery:page ? {selected:page.selected,batches:page.batches} : null}, errors };
  }
  const paths = { production:'/api/production?view=summary', human:'/api/production/human-review', artifacts:'/api/production/artifact-summaries' };
  function intelligencePaths(selected) {
    const suffix=selected ? '?batch='+encodeURIComponent(selected) : '';
    return Object.fromEntries(Object.entries({preselection:'/api/rss-triage',intelligence:'/api/intelligence-tasks',candidates:'/api/candidates'}).map(([key,path])=>[key,path+suffix+(key === 'preselection' && ['overview','ingest','triage','rank'].includes(node) ? (suffix ? '&' : '?')+new URLSearchParams({view:node === 'overview' ? 'rank' : node,...(run ? {run} : {})}) : '')]));
  }
  if (intelligence) {
    paths.discovery = '/api/discovery-run' + (batch ? '?batch=' + encodeURIComponent(batch) : '');
    paths.artifacts = '/api/production/artifact-summaries';
    if(batch) Object.assign(paths,intelligencePaths(batch));
  }
  if (operations && !intelligence) {
    const suffix = recordBatch ? '?batch=' + encodeURIComponent(recordBatch) : '';
    paths.artifacts = '/api/production/artifact-summaries';
    paths.candidates = '/api/candidates' + suffix;
    paths.intelligence = '/api/intelligence-tasks' + suffix;
    if (recordBatch) paths.discovery = '/api/discovery-run' + suffix;
  }
  const data = Object.fromEntries(await Promise.all(Object.entries(paths).map(([key,path])=>read(key,path))));
  if (intelligence && !batch) {
    const selected = batch || data.discovery?.selected || '';
    const extra = await Promise.all(Object.entries(intelligencePaths(selected)).map(([key,path])=>read(key,path)));
    Object.assign(data,Object.fromEntries(extra));
  }
  return {data, errors};
}
