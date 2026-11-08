/** 图文选区映射到准确源码；位置使用Unicode码点，禁止全文模糊替换。 */
export function articleLines(body) {
  let offset = 0;
  return body.split(/(\r?\n)/).flatMap(line => {
    const start = offset; offset += Array.from(line).length;
    if (!line.trim()) return [];
    const heading = line.startsWith('## ') && line.slice(3).trim();
    const shown = heading ? line.slice(3).trim() : line;
    return [{ text: shown, start: start + Array.from(line.slice(0, line.indexOf(shown))).length }];
  });
}

export function selectionReference(selection) {
  if (!selection?.rangeCount || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  const element = node => (node.nodeType === 1 ? node : node.parentElement)?.closest('[data-edit-target]');
  const first = element(range.startContainer), last = element(range.endContainer);
  if (!first || !last || first.dataset.editTarget !== last.dataset.editTarget) return null;
  const position = (root, node, offset) => {
    const before = root.ownerDocument.createRange();
    before.selectNodeContents(root); before.setEnd(node, offset);
    return Number(root.dataset.editStart || 0) + Array.from(before.toString()).length;
  };
  return {
    target: first.dataset.editTarget,
    start: position(first, range.startContainer, range.startOffset),
    end: position(last, range.endContainer, range.endOffset),
  };
}

export function targetText(state, target) {
  if (target === 'body') return state.draft.submission.body;
  if (target === 'title') return state.draft.submission.title;
  return state.package.presentation?.figures[Number(target.split(':')[1])]?.selection.caption || '';
}

export function addReference(references, reference) {
  if (references.some(r => r.target === reference.target && r.start === reference.start && r.end === reference.end)) return references;
  if (references.length >= 8) throw new Error('最多引用8处文字');
  if (Array.from(reference.text).length > 4000) throw new Error('单处引用最多4000字符');
  if (references.some(r => r.target === reference.target && reference.start < r.end && reference.end > r.start)) throw new Error('引用范围重叠，请先移除已有引用');
  return [...references, reference];
}
