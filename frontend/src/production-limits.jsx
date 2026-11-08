/** 新建制作任务的额度字段；已有阶段重试使用服务端冻结配置。 */
import React from 'react';
import { Collapse, InputNumber, Select } from 'antd';

export const initialLimits = { max_tokens: 16384, timeout_seconds: 1000, max_attempts: 2, max_reads: 12, reasoning_effort: 'low', max_revision_rounds: 5 };
export function LimitsFields({ value, onChange }) {
  const change = (key, next) => onChange({ ...value, [key]: next });
  return <Collapse items={[{ key: 'limits', label: '新任务额度与模型参数', children: <div className="limits-grid">
    <label>单次响应 token 上限<InputNumber aria-label="单次响应 token 上限" min={256} max={16384} value={value.max_tokens} onChange={next => change('max_tokens', next)} /></label>
    <label>单任务超时（秒）<InputNumber aria-label="单任务超时秒数" min={10} max={1000} value={value.timeout_seconds} onChange={next => change('timeout_seconds', next)} /></label>
    <label>最多读取资料次数<InputNumber aria-label="最多读取资料次数" min={1} max={50} value={value.max_reads} onChange={next => change('max_reads', next)} /></label>
    <label>全流程自动返修上限<InputNumber aria-label="全流程自动返修上限" min={1} max={10} value={value.max_revision_rounds ?? 5} onChange={next => change('max_revision_rounds', next ?? 5)} /></label>
    <label>推理强度<Select aria-label="推理强度" value={value.reasoning_effort} options={['off', 'low', 'high', 'max'].map(x => ({ value: x, label: x }))} onChange={next => change('reasoning_effort', next)} /></label>
    <p>仅用于新建任务；已有任务重试沿用冻结配置。确认操作后自动执行制作链。</p>
  </div> }]} />;
}

