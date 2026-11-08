/** 人民币估算展示；没有用量不能显示零元。 */
export function productionCostLabel(cost) {
  if (cost?.amount == null) return '费用未知';
  const amount = Number(cost.amount);
  if (!Number.isFinite(amount)) return '费用未知';
  return `约 ¥${amount.toFixed(4)}${cost.partial ? '（部分）' : ''}`;
}
