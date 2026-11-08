import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {readSourceFilters,sourceQuery,policyDefinition,safeHttpUrl} from '../../frontend/src/sources-model.js';

test('source URL roundtrip preserves server-side combination and page offset',()=>{
  const filters=readSourceFilters('?q=Example&kind=journal_site&availability=unverified&status=paused&offset=200');
  assert.deepEqual(readSourceFilters('?' + sourceQuery(filters)),filters);
  assert.equal(readSourceFilters('?offset=-1').offset,0);
  assert.equal(readSourceFilters('?offset=1.5').offset,0);
});
test('retrieval edits retain frozen scope identity and unedited definition fields',()=>{
  const baseline={scope_version:'ai-biology-v1',extra:'preserved'};
  const value=policyDefinition(baseline,{name:'new',agent_terms:' AI agent \n\nLLM agent',model_terms:'LLM',drug_terms:'protein',review_terms:''});
  assert.equal(value.scope_version,'ai-biology-v1');assert.equal(value.extra,'preserved');
  assert.deepEqual(value.agent_terms,['AI agent','LLM agent']);assert.deepEqual(value.review_terms,[]);
});
test('configuration uses real navigation and renders untrusted source details as text',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try{
    const {SourceDetails}=await server.ssrLoadModule('/src/sources-workspace.jsx');
    const {ConfigurationShell,WorkspaceNavigation}=await server.ssrLoadModule('/src/workspace-shell.jsx');
    const noCall=()=>assert.fail('render must not run a business command');
    const html=renderToStaticMarkup(React.createElement(SourceDetails,{item:{name:'<script>alert(1)</script>',collection_availability:{reason:'<img src=x>',checks:[{url:'javascript:alert(1)',mode:'homepage',reason:'x'}]}}}));
    assert.ok(!html.includes('<script>'));assert.ok(!html.includes('href="javascript:'));
    const shell=renderToStaticMarkup(React.createElement(ConfigurationShell,{api:noCall,post:noCall,active:'sources'},'Content'));
    assert.ok(shell.includes('aria-current="page" href="/sources"'));assert.ok(shell.includes('stage=production'));assert.ok(shell.includes('Content'));
    const renderRail=props=>renderToStaticMarkup(React.createElement(WorkspaceNavigation,props));
    const configRail=renderRail({active:'sources'}), businessRail=renderRail({active:'ingest'});
    const destinations=html=>[...html.matchAll(/href="([^"]+)"/g)].map(match=>match[1]);
    assert.deepEqual(destinations(businessRail),destinations(configRail));
    assert.equal((businessRail.match(/aria-current="page"/g)||[]).length,1);
    assert.ok(businessRail.includes('aria-current="page" aria-label="资料采集"'));
    assert.ok(renderRail({active:'production',stageCount:()=>3}).includes('稿件制作，3 项当前停留'));
    assert.ok(!businessRail.includes('<button'));
    const {readFile}=await import('node:fs/promises');
    const realSource=await readFile(new URL('../../frontend/src/real-workspace.jsx',import.meta.url),'utf8');
    assert.ok(realSource.includes('<WorkspaceNavigation '));
    assert.ok(!realSource.includes('<aside className="trace-rail">'));
    assert.equal(safeHttpUrl('javascript:alert(1)'),false);assert.equal(safeHttpUrl('https://example.org'),true);
  }finally{await server.close();}
});
