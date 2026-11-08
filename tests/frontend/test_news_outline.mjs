import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';

test('outline shows paragraph dependencies and exact sources without inventing legacy plans', async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {WritingResearchReport}=await server.ssrLoadModule('/src/writing-research-report.jsx');
    const span={content_hash:'hash',start:0,end:10};
    const research={answer:'研究回答',findings:[],background:[],gaps:[],research_scope:'实际读取范围'};
    const snapshot={version:2,brief_version:1,question:'新闻问题',research};
    const render=()=>renderToStaticMarkup(React.createElement(WritingResearchReport,{snapshot,excerpts:[{evidence_id:'paper',...span,text:'准确原文<script>不执行</script>'}]}));
    assert.ok(!render().includes('新闻骨架'));
    research.outline={mainline:'新闻主线内容',progression:'先解释问题，再交代结果',ending:'回答核心问题即结束',omissions:'次要实验不展开',paragraphs:[
      {section:'具体变化',question:'这个实验改变什么',message:'关键结果',position_reason:'先介绍结果',sources:[{evidence_id:'paper',source_span:span,location:'PDF页3-3',use:'支持关键结果'}],depends_on:[],visual_need:'对照图'},
      {question:'读者接下来需要知道什么',message:'承接前段',position_reason:'解释已介绍的结果',sources:[],depends_on:[1]},
    ]};
    const html=render();
    for(const value of ['新闻骨架','计划第1段','计划第2段','PDF页3-3','支持关键结果','承接计划第1段','次要实验不展开','准确原文&lt;script&gt;']) assert.ok(html.includes(value),value);
    assert.ok(!html.includes('<script>'));
  } finally { await server.close(); }
});
