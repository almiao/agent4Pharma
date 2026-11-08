import json
import subprocess
from pathlib import Path

from agent4pharma.infrastructure.llm.context_visibility import read_visibility_epoch
from agent4pharma.infrastructure.llm.patch import create_patch
from agent4pharma.modules.content.draft_contracts import EvidenceExcerpt, ResearchEvidence
from agent4pharma.runtime.context_window_policy import RevisionWindowPolicy
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.evidence_delivery import EvidenceDelivery


def test_compression_invalidates_evidence_coverage(tmp_path):
    marker = tmp_path / "epoch"
    marker.write_text("0")
    delivery = EvidenceDelivery(lambda: read_visibility_epoch(marker))
    source = ResearchEvidence(
        id="e", title="original", origin="test", text="prior work measured flow; we measured multiome"
    )
    excerpt = EvidenceExcerpt(
        evidence_id=source.id,
        content_hash=source.content_hash,
        start=0,
        end=len(source.text),
        text=source.text,
    )
    assert delivery.deliver(source, excerpt).text == source.text
    assert delivery.deliver(source, excerpt).already_read
    marker.write_text("1")
    assert delivery.deliver(source, excerpt).text == source.text
    marker.unlink()
    assert delivery.deliver(source, excerpt).text == source.text


def test_frozen_policy_patch_and_no_legacy_upgrade(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path)
    legacy = create_patch(tmp_path / "config", config, "old", "prompt", mcp_args=["bound"])
    assert "pharma-revision-window" not in legacy.read_text()
    current = create_patch(
        tmp_path / "config",
        config,
        "new",
        "prompt",
        mcp_args=["bound"],
        revision_window_policy=RevisionWindowPolicy(),
    )
    rows = json.loads(current.read_text())
    plugin = rows[-1]["insert"][0]
    assert plugin["config"]["threshold_tokens"] == 40000
    assert rows[-2]["config"]["auto"] is False


def test_runtime_window_prunes_only_obsolete_candidates_and_keeps_tool_pairs():
    plugin = Path("src/agent4pharma/infrastructure/llm/dsh_revision_window.cjs").resolve()
    code = r"""
const assert = require('node:assert/strict');
const p = require(process.argv[1]);
const old = {version: 1, base_content_hash: 'a', candidate_hash: 'b', body: 'old',
    issues: {required_ids: ['RR-04']}, resources: ['exact rules'], context_receipt: 'old receipt'};
const changed = p.obsolete(old, 2);
assert.equal(changed.body, undefined);
assert.deepEqual(changed.issues, old.issues);
assert.deepEqual(changed.resources, old.resources);
assert.equal(changed.context_receipt, undefined);
assert.equal(p.obsolete(old, 1), null);
assert.equal(p.obsolete({evidence_id:'e',text:'critical middle source'}, 2), null);
const types = ['system/message','user/message','assistant/message','tool/result',
    'tool/result','assistant/message','tool/result'];
const session = {eventAt: seq => ({type:types[seq]})};
const measurement = {nodes: types.map((t,seq)=>({seq,tokens:100}))};
assert.deepEqual(p.compactRange(session,measurement,150),[1,4]);
assert.equal(p.compactRange(session,measurement,1000), null);
"""
    subprocess.run(["node", "-e", code, str(plugin)], check=True, capture_output=True, text=True)


def test_runtime_budget_deduplicates_usage_and_never_starts_an_extra_call(tmp_path):
    plugin = Path("src/agent4pharma/infrastructure/llm/dsh_revision_window.cjs").resolve()
    code = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const p = require(process.argv[1]);
process.env.PHARMA_CONTEXT_EPOCH = process.argv[2];
const hooks = {};
const ctx = {on: (name, cb) => {hooks[name] = cb}, tokenMeter: {measure: () => ({totalTokens:0})}};
p.apply(ctx, {max_requests:2,max_total_tokens:10000,max_output_tokens:1000,threshold_tokens:40000});
const session = {surface:{nodes:[]}};
const data = {turn:1,step:1,usage:{inputTokens:10,cacheReadTokens:100,outputTokens:5,totalTokens:115}};
hooks['session/event'](session,{type:'assistant/attempt',data});
hooks['session/event'](session,{type:'assistant/message',data});
(async () => {
  let calls = 0;
  await hooks['agent/pre-step']({agent:{session},signal:{}},()=>{calls++});
  assert.equal(calls,1);
  hooks['session/event'](session,{type:'llm/retry-started',data:{}});
  hooks['session/event'](session,{type:'assistant/message',data});
  await assert.rejects(hooks['agent/pre-step']({agent:{session},signal:{}},()=>{calls++}),
    /REVISION_EXECUTION_BUDGET/);
  assert.equal(calls,1);
  assert.equal(fs.readFileSync(process.argv[2]+'.budget','utf8'),'REVISION_EXECUTION_BUDGET');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(
        ["node", "-e", code, str(plugin), str(tmp_path / "epoch")], check=True, capture_output=True
    )
