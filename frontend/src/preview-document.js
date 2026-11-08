/** 将宿主图文包转成无脚本的隔离预览文档。 */
export function previewDocument(body, variant) {
  const markup = variant === 'wechat'
    ? '<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;padding:24px 20px;background:white;overflow-wrap:anywhere}*{box-sizing:border-box}img{max-width:100%;height:auto}</style></head><body>' + body + '</body></html>' : body;
  const doc = new DOMParser().parseFromString(markup, 'text/html');
  // srcdoc继承父页CSP；仅给本地模板样式附当前nonce，脚本仍由空sandbox禁止。
  const nonce = document.querySelector('meta[name="style-nonce"]')?.content || '';
  const rules = [];
  doc.querySelectorAll('[style]').forEach((node, index) => {
    const name = 'preview-inline-' + index;
    node.classList.add(name); rules.push('.' + name + '{' + node.getAttribute('style') + '}'); node.removeAttribute('style');
  });
  const styles = doc.createElement('style'); styles.textContent = rules.join('\n'); doc.head.appendChild(styles);
  doc.querySelectorAll('style').forEach(style => style.setAttribute('nonce', nonce));
  doc.querySelectorAll('script,iframe,object,embed,base,form,link').forEach(node => node.remove());
  return '<!doctype html>' + doc.documentElement.outerHTML;
}

