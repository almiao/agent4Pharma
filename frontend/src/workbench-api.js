/** 合并同时发生的同源只读请求；不缓存已完成状态，修改前后隔离在途读取。 */
export function createWorkbenchApi(token, fetcher = fetch) {
  const pending = new Map();
  let revision = 0;
  async function request(path, body) {
    const response = await fetcher(path, {
      headers: {'X-Workbench-Token':token, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
      ...(body === undefined ? {} : {method:'POST',body:JSON.stringify(body)}),
      signal:AbortSignal.timeout(20000),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
    return value;
  }
  function api(path) {
    const key = `${revision}:${path}`;
    if (!pending.has(key)) {
      const reading = request(path).finally(() => pending.delete(key));
      pending.set(key, reading);
    }
    return pending.get(key);
  }
  async function post(path, body) {
    revision++;
    try { return await request(path, body); }
    finally { revision++; }
  }
  return {api, post};
}
