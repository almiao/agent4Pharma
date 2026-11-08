'use strict';
// Keep immutable audit events; only replace the model-facing surface through DSH's public transaction API.
const fs = require('node:fs');
exports.name = 'pharma-revision-window';
exports.inject = ['compaction', 'tokenMeter'];

function payload(block) {
  if (block.type !== 'text') return null;
  try { return JSON.parse(block.text); } catch { return null; }
}

function obsolete(value, version) {
  if (!value || !Number.isInteger(value.version) || value.version >= version) return null;
  const copy = { ...value };
  if (value.base_content_hash && value.candidate_hash && typeof value.body === 'string') {
    delete copy.body; // Keep the task brief, exact active issues and mandatory standards.
    delete copy.related_claims;
  } else if (['body', 'claims', 'diff', 'metadata', 'history'].includes(value.section)) {
    delete copy.text;
    delete copy.claims;
  } else if (value.state && Array.isArray(value.feedback)) {
    delete copy.feedback;
    delete copy.next_action;
  } else return null;
  delete copy.context_receipt; // A changed payload must never claim delivery of the original receipt.
  copy.superseded_by_version = version;
  copy.reload = 'read_revision_candidate：旧候选内容已卸载，按需读取最新版本。原记录仍在执行追踪中。';
  return copy;
}
exports.obsolete = obsolete;

function compactRange(session, measurement, retain) {
  const nodes = measurement.nodes;
  let budget = 0, keep = nodes.length;
  while (keep > 0 && budget < retain) budget += nodes[--keep].tokens;
  // A new assistant response starts only after the preceding step's complete tool-result group.
  while (keep > 0 && session.eventAt(nodes[keep].seq)?.type !== 'assistant/message') keep--;
  const start = session.eventAt(nodes[0]?.seq)?.type === 'system/message' ? 1 : 0;
  if (keep <= start + 1) return null;
  return [nodes[start].seq, nodes[keep - 1].seq];
}
exports.compactRange = compactRange;

exports.apply = function(ctx, config) {
  const states = new WeakMap();
  const epochFile = process.env.PHARMA_CONTEXT_EPOCH;
  if (!epochFile) throw new Error('revision-window: missing visibility epoch path');
  function state(session) {
    if (!states.has(session)) states.set(session, {
      calls: 0, total: 0, output: 0, compactions: 0, epoch: 0, retry: 0, samples: new Map(),
    });
    return states.get(session);
  }
  ctx.on('session/event', (session, event) => {
    const s = state(session);
    if (event.type === 'llm/retry-started') s.retry++;
    if (event.surfaceOp?.op === 'replace') fs.writeFileSync(epochFile, String(++s.epoch));
    if (!['assistant/message', 'assistant/attempt', 'compaction/summary'].includes(event.type)) return;
    const usage = event.data.usage || event.data.stream?.find(x => x.type === 'usage')?.usage;
    if (usage) {
      const key = event.type === 'compaction/summary' ? `compact:${event.seq}`
        : `${event.data.turn}:${event.data.step}:${s.retry}`;
      s.samples.set(key, usage);
      s.calls = s.samples.size;
      s.output = [...s.samples.values()].reduce((n, u) => n + (u.outputTokens || 0), 0);
      s.total = [...s.samples.values()].reduce((n, u) => n + (u.totalTokens
        ?? ((u.inputTokens || 0) + (u.cacheReadTokens || 0) + (u.outputTokens || 0))), 0);
    }
  });
  ctx.on('agent/pre-step', async ({ agent, signal }, next) => {
    const session = agent.session, s = state(session);
    if (s.calls >= config.max_requests || s.total >= config.max_total_tokens || s.output >= config.max_output_tokens) {
      fs.writeFileSync(epochFile + '.budget', 'REVISION_EXECUTION_BUDGET');
      throw new Error('REVISION_EXECUTION_BUDGET: 累计执行额度已达到；保留已保存候选，禁止自动重试。');
    }
    const candidates = session.surface.nodes.map(seq => ({ seq, event: session.eventAt(seq) }))
      .filter(x => x.event?.type === 'tool/result');
    let version = 0;
    for (const { event } of candidates) for (const block of event.data.message.content) {
      const value = payload(block);
      if (value && (value.candidate_hash || value.section || value.state) && Number.isInteger(value.version)) {
        version = Math.max(version, value.version);
      }
    }
    for (const { seq, event } of candidates) {
      const original = session.deriveEventMessage(event);
      let changed = false;
      const content = original.content.map(block => {
        const value = obsolete(payload(block), version);
        if (!value) return block;
        const text = JSON.stringify(value);
        if (text.length >= block.text.length) return block;
        changed = true;
        return { ...block, text };
      });
      if (!changed) continue;
      session.append('compaction/prune', {
        shadowedRange: { start: seq, end: seq }, shadowedSeqs: [seq],
        shadowedTokenCount: ctx.tokenMeter.estimateMessage(original),
      });
      session.append('tool/result', { ...event.data, message: { ...original, content } }, {
        surfaceOp: { op: 'replace', startSeq: seq, endSeq: seq }, sourceEventSeqs: [seq],
      });
      // Invalidate synchronously even if a runtime does not forward surfaceOp in event callbacks.
      fs.writeFileSync(epochFile, String(++s.epoch));
    }
    const measured = ctx.tokenMeter.measure(session);
    if (measured.totalTokens >= config.threshold_tokens && s.compactions < config.max_compactions) {
      const range = compactRange(session, measured, config.retain_tokens);
      if (range) {
        await ctx.compaction.compactRegion(range[0], range[1], agent, signal);
        s.compactions++;
        fs.writeFileSync(epochFile, String(++s.epoch));
      }
    }
    return next();
  });
};
