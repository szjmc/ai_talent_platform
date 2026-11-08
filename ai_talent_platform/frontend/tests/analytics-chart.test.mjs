import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toChartOption } from '../src/utils/analyticsChart.ts';

test('maps backend xy config and preserves null and zero', () => {
  const option = toChartOption({ type: 'bar', title: '人数', x_labels: ['A', 'B'], series: [{ name: '人数', data: [{ name: 'A', value: 0 }, { name: 'B', value: null }] }] });
  assert.deepEqual(option.xAxis.data, ['A', 'B']);
  assert.deepEqual(option.series[0].data, [0, null]);
});
test('maps pie, radar and heatmap without executable code', () => {
  assert.equal(toChartOption({ type: 'pie', title: '', series: [{ name: 'n', data: [{ name: 'x', value: 2 }] }] }).series[0].data[0].value, 2);
  const radar = toChartOption({ type: 'radar', title: '', indicators: [{ name: 'A', max: 10 }], series: [{ name: 'n', data: [{ name: 'A', value: 3 }] }] });
  assert.deepEqual(radar.series[0].data[0].value, [3]);
  const heatmap = toChartOption({ type: 'heatmap', title: '', series: [], x_labels: ['x'], y_labels: ['y'], values: [[0, 0, -4]] });
  assert.equal(heatmap.visualMap.min, -4);
  assert.equal(toChartOption(null), undefined);
});
