/** 失败原因与处理任务入口；浏览、展开均不重试模型。 */
import React from 'react';
import { Alert, Button } from 'antd';
import { paperProcessingFailure } from './paper-processing-failure.js';

export default function PaperProcessingFailure({ paper, data }) {
  const failure = paperProcessingFailure(paper, data);
  if (!failure) return null;
  return <Alert type="error" showIcon title="采编处理失败" description={<>
    <p>{failure.message}</p>
    {failure.task ? <><p>关联任务：{failure.phase} · {failure.state}</p>{failure.task.error && failure.task.error !== paper.continuation_error && <p>{failure.task.error}</p>}</> : <p>尚未读取任务详情，失败阶段待确认。</p>}
    {failure.href && <Button type="link" href={failure.href}>查看处理任务</Button>}
  </>} />;
}
