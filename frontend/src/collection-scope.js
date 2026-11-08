/** 大集合的浏览器会话引用，保持精确成员；丢失引用时禁止退化为全库查询。 */
export function storeCollection(url, storage) {
  const ids = url.searchParams.get('ids');
  if (ids === null || ids.length <= 3000) return url;
  const key = crypto.randomUUID();
  try { storage.setItem('console-collection/' + key, JSON.stringify(ids.split(',').filter(Boolean))); }
  catch { /* URL 留下不可恢复引用，目标页显式报未知，不扩大范围。 */ }
  url.searchParams.delete('ids'); url.searchParams.set('collection', key);
  return url;
}

export function restoreCollection(query, storage) {
  if (query.has('ids')) return { ids: new Set(query.get('ids').split(',').filter(Boolean)), missing: false };
  if (!query.has('collection')) return { ids: null, missing: false };
  try {
    const value = JSON.parse(storage.getItem('console-collection/' + query.get('collection')));
    if (Array.isArray(value) && value.every(id => typeof id === 'string')) return { ids: new Set(value), missing: false };
  } catch { /* 引用无法恢复时阻止浏览错误集合。 */ }
  return { ids: new Set(), missing: true };
}
