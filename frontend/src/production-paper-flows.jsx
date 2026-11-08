/** 未立项论文也显示完整阶段链，保存点只来自成功提交的真实任务。 */
import React from 'react';
import { Tooltip } from 'antd';
import { CircleCheck, Clock3, LoaderCircle, CircleX, CircleAlert, CirclePause, CircleMinus, CircleHelp, ChevronRight } from 'lucide-react';
import {taskLabels, productionTaskLabel} from './real-workspace-model';
import {productionCostLabel} from './production-cost';

const statusIcons = { succeeded: CircleCheck, queued: Clock3, running: LoaderCircle, failed: CircleX, blocked: CircleAlert, paused: CirclePause, cancelled: CircleMinus };

/** 保留后台任务追加顺序，每次执行都能直接定位。 */
export function ProductionStageTasks({tasks, selected, onTask, savepoints = [], layout = 'rail'}) {
  return <nav aria-label="制作执行顺序" className={`production-stage-nav ${layout === 'tabs' ? 'production-stage-tabs' : ''}`}>
    {tasks.map(task => {
      const label = productionTaskLabel(task);
      const saved = savepoints.includes(task.id) ? ' · 已保存' : '';
      const status = `${taskLabels[task.status] || task.status} · 尝试 ${task.attempt || 0}${saved} · ${productionCostLabel(task.cost)}`;
      return <div key={task.id}>
        <Tooltip title={status} trigger={['hover', 'focus']}>
          <button className={`production-stage-button stage-${task.status}`} aria-label={`${label} · ${status}`} aria-pressed={task.id === selected} onClick={()=>onTask(task.id)}>
            {React.createElement(statusIcons[task.status] || CircleHelp, { size: 19, className: 'stage-status-icon', 'aria-hidden': true })}
            <span>{label}<small className="stage-cost">{productionCostLabel(task.cost)}</small></span><ChevronRight size={15} className="stage-selection-icon" aria-hidden="true"/>
          </button>
        </Tooltip>
      </div>;
    })}
  </nav>;
}

export function PaperFlowTasks({flow, tasks, selected, navigate, layout = 'tabs'}) {
  const rows = tasks.filter(t=>flow.task_ids.includes(t.id));
  return <section aria-label="论文制作阶段">{layout === 'tabs' && <h2>{flow.paper_titles.join(' / ') || flow.candidate_ids.join(' / ')}</h2>}
    <ProductionStageTasks tasks={rows} selected={selected} savepoints={flow.savepoint_task_ids} layout={layout} onTask={id=>navigate({stage:'production',section:'',story:'',task:id})}/>{flow.story_ids.map(id=><button key={id} onClick={()=>navigate({stage:'production',section:'',story:id,task:''})}>查看已立项选题</button>)}
  </section>;
}

