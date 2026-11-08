/** 新工作台中的候选、任务书、稿件、核验与交付记录；复用原业务表单和版本校验。 */
import React from 'react';
import StageWorkspace from './stage-workspace';

export default function ProductionRecords({ role, stage, navigate, ...props }) {
  const choices = stage === 'selection' ? [['intel','论文处理记录']] : [
    ['editorial','候选与选题决定'], ['content','稿件与写作任务'],
    ['review','阅读评审 / 事实核验'], ['publishing','渠道包与导出回执'],
  ];
  return <section>
    <div className="batch-toolbar"><label>记录类别 <select aria-label="记录类别" value={role} onChange={e => navigate({stage:({editorial:'production',content:'production',review:'human',publishing:'delivery'})[e.target.value] || stage,role:e.target.value,work:'',task:'',story:''})}>{choices.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label></div>
    <StageWorkspace {...props} key={role} stageId={role} embedded />
  </section>;
}
