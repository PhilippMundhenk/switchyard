import { get } from '../api.js';
import { clear, h } from '../util.js';
import { toast } from '../ui.js';

export async function renderLog(main, ctx) {
  let last = 0;
  let paused = false;
  let entries = [];
  const devices = (await get('/devices')).devices;
  const filter = h('select', { id: 'log-filter', 'aria-label': 'Switch', 'data-testid': 'log-filter' }, h('option', { value: '' }, 'All switches'),
    ...devices.map((d) => h('option', { value: d.mac }, d.label || d.info.name || d.mac)));
  const body = h('tbody', { 'data-testid': 'log-body' });
  const pause = h('button', { class: 'btn', 'data-testid': 'log-pause' }, 'Pause');
  const copy = h('button', { class: 'btn', 'data-testid': 'log-copy' }, 'Copy for bug report');
  const clearBtn = h('button', { class: 'btn', 'data-testid': 'log-clear' }, 'Clear');
  main.append(
    h('div', { class: 'page-head' }, h('h1', {}, 'Activity'), filter, pause, clearBtn, copy),
    h('p', { class: 'muted' }, 'Every message sent to or received from a switch. Passwords are blanked. Click a row for the raw bytes.'),
    h('div', { class: 'card table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ...['Time', 'Dir', 'Peer', 'Switch', 'Message', 'Bytes'].map((t) => h('th', {}, t)))), body)));

  const draw = () => {
    clear(body);
    const rows = entries.filter((e) => !filter.value || e.device === filter.value).slice(-300).reverse();
    for (const e of rows) {
      const hex = h('pre', { class: 'hex', hidden: true }, e.hex.replace(/(.{2})/g, '$1 ').trim());
      const tr = h('tr', { class: `log-row ${e.direction}`, 'data-testid': 'log-row', tabindex: '0' },
        h('td', {}, new Date(e.time * 1000).toLocaleTimeString()), h('td', {}, e.direction === 'tx' ? '→ tx' : '← rx'),
        h('td', {}, e.peer), h('td', {}, e.device || '–'), h('td', {}, e.summary, hex), h('td', { class: 'num' }, String(e.bytes)));
      const toggle = () => { hex.hidden = !hex.hidden; };
      tr.addEventListener('click', toggle);
      tr.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') toggle(); });
      body.append(tr);
    }
  };
  const fetchMore = async () => {
    if (paused) return;
    const { entries: more } = await get(`/log?after=${last}`);
    if (more.length) { entries = entries.concat(more); last = more[more.length - 1].id; draw(); }
  };
  filter.addEventListener('change', draw);
  pause.addEventListener('click', () => { paused = !paused; pause.textContent = paused ? 'Resume' : 'Pause'; });
  clearBtn.addEventListener('click', () => { entries = []; draw(); });
  copy.addEventListener('click', async () => {
    const text = entries.filter((e) => !filter.value || e.device === filter.value).slice(-100)
      .map((e) => `${new Date(e.time * 1000).toISOString()} ${e.direction} ${e.peer} ${e.device || '-'} ${e.summary} | ${e.hex}`).join('\n');
    try { await navigator.clipboard.writeText(text); toast('Copied.'); } catch (err) { toast('Copy is not available in this browser.', 'error'); }
  });
  await fetchMore();
  const timer = setInterval(() => fetchMore().catch(() => {}), 2000);
  ctx.onCleanup(() => clearInterval(timer));
}
