/** 使用持久化版本时间展示北京时间，不用任务时间代替产物时间。 */
import React from 'react';

export default function GeneratedTime({ value, empty = '未记录' }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <span>{empty}</span>;
  return <time dateTime={value} title="北京时间">{new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })}</time>;
}
