import { get } from '../api.js';
import { clear, h } from '../util.js';

const LABELS = { link_up: 'link up', link_down: 'link down', speed_change: 'speed', online: 'online', offline: 'offline' };

export async function renderEvents(main, ctx) {
  let last = 0;
  let events = [];
  const devices = (await get('/devices')).devices;
  const filter = h('select', { 'aria-label': 'Switch', 'data-testid': 'events-filter' }, h('option', { value: '' }, 'All switches'),
    ...devices.map((d) => h('option', { value: d.mac }, d.label || d.info.name || d.mac)));
  const body = h('tbody', { 'data-testid': 'events-body' });
  main.append(
    h('div', { class: 'page-head' }, h('h1', {}, 'Events'), filter),
    h('p', { class: 'muted' }, 'Link changes and switches going offline or coming back, seen by the background monitor.'),
    h('div', { class: 'card table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ...['Time', 'Switch', 'Event', 'Details'].map((t) => h('th', {}, t)))), body)),
  );
  const draw = () => {
    clear(body);
    const rows = events.filter((e) => !filter.value || e.mac === filter.value).slice(-300).reverse();
    if (!rows.length) body.append(h('tr', {}, h('td', { colspan: '4', class: 'muted', 'data-testid': 'events-empty' }, 'Nothing yet.')));
    for (const e of rows) {
      body.append(h('tr', { class: 'event-row', 'data-testid': 'event-row' },
        h('td', {}, new Date(e.time * 1000).toLocaleString()), h('td', {}, e.switch), h('td', {}, h('span', { class: `tag ${e.type}` }, LABELS[e.type] || e.type)), h('td', {}, e.text)));
    }
  };
  const fetchMore = async () => {
    const { events: more } = await get(`/events?after=${last}`);
    if (more.length) { events = events.concat(more); last = more[more.length - 1].id; }
    draw();
  };
  filter.addEventListener('change', draw);
  await fetchMore();
  const timer = setInterval(() => fetchMore().catch(() => {}), 4000);
  ctx.onCleanup(() => clearInterval(timer));
}
