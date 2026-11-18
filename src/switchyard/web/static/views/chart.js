// A tiny dependency-free line chart (SVG) for traffic history.
import { fmtRate } from '../util.js';

const NS = 'http://www.w3.org/2000/svg';
const el = (name, attrs = {}, text) => {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
};

export function niceMax(value) {
  if (!value || value <= 0) return 1000;
  const pow = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) if (value <= step * pow) return step * pow;
  return 10 * pow;
}

// points: [[t, rx_bps, tx_bps], ...]
export function drawChart(points, { width = 760, height = 220 } = {}) {
  const pad = { l: 64, r: 12, t: 10, b: 24 };
  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, class: 'chart', role: 'img', 'aria-label': 'Traffic over time', 'data-testid': 'chart' });
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const max = niceMax(Math.max(0, ...points.flatMap((p) => [p[1], p[2]])));
  const t0 = points.length ? points[0][0] : 0;
  const t1 = points.length > 1 ? points[points.length - 1][0] : t0 + 1;
  const x = (t) => pad.l + ((t - t0) / (t1 - t0 || 1)) * w;
  const y = (v) => pad.t + h - (v / max) * h;
  for (let i = 0; i <= 4; i += 1) {
    const v = (max / 4) * i;
    svg.append(el('line', { x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: 'grid' }));
    svg.append(el('text', { x: pad.l - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }, fmtRate(v / 8)));
  }
  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (points.length) {
    svg.append(el('text', { x: pad.l, y: height - 6, class: 'axis' }, clock(t0)));
    svg.append(el('text', { x: width - pad.r, y: height - 6, 'text-anchor': 'end', class: 'axis' }, clock(t1)));
  }
  const line = (index, cls) => svg.append(el('polyline', { points: points.map((p) => `${x(p[0]).toFixed(1)},${y(p[index]).toFixed(1)}`).join(' '), class: `series ${cls}`, 'data-series': cls }));
  line(1, 'rx');
  line(2, 'tx');
  return svg;
}
