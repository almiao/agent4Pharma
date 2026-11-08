"""当前论文的固定浏览器步骤；页面内容和模型都不能生成脚本、网址或下载路径。"""

import json
import re
from pathlib import Path

from agent4pharma.modules.materials.paper_downloads import PaperDownloadRequest

CHECK_SESSION = """async (page) => {
  const current = new URL(page.url());
  if (current.origin !== 'https://www.chaoslib.com') return {status:'select_chaoslib_tab'};
  const blocked = await page.getByText(/检测到自动化访问|已被暂时封禁|账号被临时限制/).count();
  if (blocked) return {status:'browser_access_blocked'};
  if (await page.locator('input[type=password]:visible').count()) return {status:'human_login_required'};
  return {status: await page.getByRole('button', {name:/^打开 /}).count() ? 'ready' : 'human_login_required'};
}"""


def acquisition_script(request: PaperDownloadRequest, output: Path) -> str:
    """生成一次论文获取步骤；仅支持已明确验证过路由的 Nature／Springer。"""
    doi = request.identifier
    if not re.fullmatch(r"10\.(1038|1007)/[A-Za-z0-9][A-Za-z0-9._();:-]*", doi):
        raise ValueError("daily_browser_publisher_not_supported")
    nature = doi.startswith("10.1038/")
    args = json.dumps(
        {
            "button": "打开 Nature(自然杂志)" if nature else "打开 SpringerLink",
            "family": "nature" if nature else "springer",
            "path": "/articles/" + doi.split("/", 1)[1] if nature else "/article/" + doi,
            "file": str(output.resolve()),
        },
        ensure_ascii=False,
    )
    return """async (page) => {
  const task = ARGS;
  let step = 'catalog';
  try {
  const allowed = value => {
    const url = new URL(value);
    return url.protocol === 'https:' && (
      ['www.nature.com','nature.com','link.springer.com','link.springer.nature.com'].includes(url.hostname)
      || /^(nature|springer2)\\.66557\\.net$/.test(url.hostname));
  };
  if (new URL(page.url()).origin !== 'https://www.chaoslib.com') return {status:'select_chaoslib_tab'};
  if (await page.locator('input[type=password]:visible').count()) return {status:'human_login_required'};
  const publisherPages = async () => {
    const found = [];
    for (const candidate of page.context().pages()) {
      if (candidate === page || candidate.isClosed()) continue;
      const opener = await candidate.opener();
      if (opener !== page && (!opener || await opener.opener() !== page)) continue;
      const value = candidate.url();
      if (!allowed(value)) continue;
      const host = new URL(value).hostname;
      if (task.family === 'nature' ? ['nature.com','www.nature.com','nature.66557.net'].includes(host)
          : ['link.springer.com','link.springer.nature.com','springer2.66557.net'].includes(host))
        found.push(candidate);
    }
    return found;
  };
  let candidates = await publisherPages();
  if (candidates.length > 1) return {status:'publisher_tabs_require_human'};
  let paper = candidates[0];
  if (!paper) {
    await page.getByText('英文数据库', {exact:true}).click();
    step = 'publisher_entry';
    const entry = page.getByRole('button', {name:task.button, exact:true});
    if (!await entry.count() || !await entry.isEnabled()) return {status:'publisher_entry_unavailable'};
    await entry.click();
    step = 'publisher_navigation';
    // 人工确认可能在第二层新标签页打开出版商；不自动点击验证按钮。
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      candidates = await publisherPages();
      if (candidates.length > 1) return {status:'publisher_tabs_require_human'};
      if (candidates.length === 1) { paper = candidates[0]; break; }
      await new Promise(resolve => setTimeout(resolve,1000));
    }
    if (!paper) return {status:'publisher_confirmation_required', step};
  }
  await paper.waitForLoadState('domcontentloaded', {timeout:30000});
  const origin = new URL(paper.url());
  if (!allowed(paper.url())) return {status:'publisher_origin_requires_human'};
  await new Promise(resolve => setTimeout(resolve,30000));
  step = 'article_navigation';
  const response = await paper.goto(new URL(task.path, origin.origin).href,
    {waitUntil:'domcontentloaded',timeout:30000});
  if (response && response.status() === 404)
    return {status:'publisher_article_not_found', step, http_status:404};
  if (!allowed(paper.url())) return {status:'publisher_origin_requires_human'};
  if (await paper.getByText(/Just a moment|安全验证|验证您是真人|访问被拒绝/).count())
    return {status:'publisher_verification_required'};
  const pdf = paper.getByRole('link', {name:/^Download PDF$/i}).first();
  step = 'pdf_link';
  if (!await pdf.count()) return {status:'pdf_link_requires_human'};
  const href = await pdf.getAttribute('href');
  if (!href || !allowed(new URL(href, paper.url()).href)) return {status:'pdf_link_requires_human'};
  await new Promise(resolve => setTimeout(resolve,30000));
  try {
    step = 'download_wait';
    const [download] = await Promise.all([paper.waitForEvent('download', {timeout:30000}), pdf.click()]);
    step = 'save_file';
    await download.saveAs(task.file);
    // 保留人工打开的出版商标签，便于继续检查。
    return {status:'file_saved', step:'save_file'};
  } catch {
    return {status:'pdf_save_requires_human', step};
  }
  } catch (error) {
    return {status:'browser_action_failed', step,
      error_kind:error && error.name === 'TimeoutError' ? 'timeout' : 'action_error'};
  }
}""".replace("ARGS", args, 1)
