/** 公开执行文本的轻量排版；仅识别常用Markdown，不执行HTML或加载远程图片。 */
import React from 'react';

function inline(text) {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/(?:[^\s()]|\([^()\s]*\))+\))/g).map((part,index)=>{
    if(part.startsWith('`') && part.endsWith('`'))return <code key={index}>{part.slice(1,-1)}</code>;
    if(part.startsWith('**') && part.endsWith('**'))return <strong key={index}>{part.slice(2,-2)}</strong>;
    const link=part.match(/^\[([^\]]+)\]\((https?:\/\/(?:[^\s()]|\([^()\s]*\))+)\)$/);
    if(link)return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return part;
  });
}

export default function ExecutionText({text}) {
  const lines=text.split('\n'),blocks=[];
  let index=0;
  while(index<lines.length) {
    const line=lines[index];
    if(!line.trim()){index++;continue;}
    if(/^\s*```/.test(line)) {
      const language=line.trim().slice(3),code=[];index++;
      while(index<lines.length && !/^\s*```/.test(lines[index]))code.push(lines[index++]);
      if(index<lines.length)index++;
      blocks.push(<pre key={blocks.length} aria-label={language?`${language} 代码`:'代码'}><code>{code.join('\n')}</code></pre>);continue;
    }
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading){blocks.push(<h4 key={blocks.length}>{inline(heading[2])}</h4>);index++;continue;}
    const list=line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);
    if(list) {
      const ordered=/^\s*\d+\./.test(line),items=[];
      while(index<lines.length) {
        const match=lines[index].match(ordered?/^\s*\d+\.\s+(.+)$/:/^\s*[-*]\s+(.+)$/);
        if(!match)break;
        items.push(<li key={items.length}>{inline(match[1])}</li>);index++;
      }
      blocks.push(ordered?<ol key={blocks.length} start={Number(line.trim().match(/^\d+/)[0])}>{items}</ol>:<ul key={blocks.length}>{items}</ul>);continue;
    }
    if(line.trim().startsWith('|')) {
      const rows=[];
      while(index<lines.length && lines[index].trim().startsWith('|'))rows.push(lines[index++]);
      blocks.push(<pre key={blocks.length}>{rows.join('\n')}</pre>);continue;
    }
    const paragraph=[line];index++;
    while(index<lines.length && lines[index].trim() && !/^(?:#{1,6}\s|\s*```|\s*[-*]\s|\s*\d+\.\s|\s*\|)/.test(lines[index]))paragraph.push(lines[index++]);
    blocks.push(<p key={blocks.length}>{inline(paragraph.join('\n'))}</p>);
  }
  return <div className="execution-prose">{blocks}</div>;
}
