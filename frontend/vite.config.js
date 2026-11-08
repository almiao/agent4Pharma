import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  root: import.meta.dirname,
  plugins: [{
    name: 'console-csp-adaptations',
    enforce: 'pre',
    transform(code, id) {
      // 固定的上游测量/滚动锁调用补 nonce，文本框测量改用 CSSOM；不放宽 CSP。
      const path = id.replaceAll('\\', '/');
      const adaptations = [
        ['/util/es/getScrollBarSize.js', '}`, randomId);', "}`, randomId, { csp: { nonce: document.querySelector('meta[name=style-nonce]')?.content } });"],
        ['/portal/es/useScrollLocker.js', '}`, id);', "}`, id, { csp: { nonce: document.querySelector('meta[name=style-nonce]')?.content } });"],
        ['/input/es/calculateNodeHeight.js', "hiddenTextarea.setAttribute('style', `${sizingStyle};${HIDDEN_TEXTAREA_STYLE}`);", 'hiddenTextarea.style.cssText = `${sizingStyle};${HIDDEN_TEXTAREA_STYLE}`;'],
      ];
      for (const [suffix, original, replacement] of adaptations) {
        if (!path.includes('/@rc-component') || !path.endsWith(suffix)) continue;
        if (!code.includes(original)) throw new Error('Upstream CSP adaptation changed: ' + suffix);
        return code.replace(original, replacement);
      }
    },
  }],
  base: '/console-assets/',
  build: {
    outDir: resolve(import.meta.dirname, '../src/agent4pharma/entrypoints/web/static/live-console'),
    emptyOutDir: true,
    rolldownOptions: { input: resolve(import.meta.dirname, 'index.html') },
  },
});
