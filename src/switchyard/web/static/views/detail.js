// One switch: loads its state and hosts the tabs.
import { dev, get } from '../api.js';
import { clear, h } from '../util.js';
import { showError } from '../ui.js';
import { TABS, entrySection } from './tabs.js';

export async function renderDetail(main, ctx, route) {
  const mac = route.mac;
  const tab = TABS.find((t) => t.id === route.tab) || TABS[0];
  const host = h('div', { 'data-testid': 'tab-body' });
  const header = h('div', { class: 'page-head' });
  const tabs = h('nav', { class: 'tabs', 'aria-label': 'Switch sections' },
    ...TABS.map((t) => h('a', { href: `#/switch/${encodeURIComponent(mac)}/${t.id}`, 'aria-current': t.id === tab.id ? 'page' : null, 'data-testid': `tab-${t.id}` }, t.label)));
  const picker = h('select', { class: 'tab-select', 'aria-label': 'Section', 'data-testid': 'tab-select' },
    ...TABS.map((t) => h('option', { value: t.id, selected: t.id === tab.id }, t.label)));
  picker.addEventListener('change', () => ctx.navigate(`#/switch/${encodeURIComponent(mac)}/${picker.value}`));
  main.append(h('p', { class: 'back' }, h('a', { href: '#/' }, '← All switches')), header, tabs, picker, host);

  const S = {
    mac, state: null, ctx, host,
    get name() {
      const d = this.state && this.state.device;
      return d ? d.label || d.info.name || d.info.model || mac : mac;
    },
    async reload() {
      this.state = await get(`${dev(mac)}/state`);
      return this.state;
    },
    async show() {
      clear(host);
      try {
        await tab.render(host, S);
      } catch (err) {
        clear(host);
        host.append(unreachable(err, () => { clear(host); S.reload().then(() => S.show()).catch((e) => { clear(host); host.append(unreachable(e, () => window.location.reload())); }); }));
        showError(err);
      }
    },
  };

  function title() {
    clear(header);
    const d = S.state.device;
    header.append(h('h1', { 'data-testid': 'switch-title' }, S.name),
      h('span', { class: 'tag' }, `${S.state.info.model || '?'} · ${mac}`));
  }

  try {
    await S.reload();
    title();
  } catch (err) {
    header.append(h('h1', {}, mac));
    host.append(unreachable(err, () => window.location.reload()));
    if (err.code === 'not_found') return;
    showError(err);
    // the entry itself still works: a fixed address or removing it are often the way out
    const device = await get(dev(mac)).catch(() => null);
    if (device) {
      S.state = { device, info: device.info || {} };
      host.append(entrySection(device, S, () => window.location.reload()));
    }
    return;
  }
  S.retitle = title;
  // phone and desktop use different layouts for some sections: redraw when the width changes
  const mq = window.matchMedia('(max-width: 640px)');
  const onWidth = () => S.show();
  mq.addEventListener('change', onWidth);
  ctx.onCleanup(() => mq.removeEventListener('change', onWidth));
  await S.show();
}

function unreachable(err, retry) {
  return h('div', { class: 'card empty', 'data-testid': 'unreachable' },
    h('h2', {}, err.code === 'not_found' ? 'Unknown switch' : 'Switch not reachable'),
    h('p', {}, err.message),
    err.code === 'not_found' ? null : h('p', { class: 'muted' }, 'Check power and cabling. If Switchyard runs in another network segment or a bridged container, set the switch\'s fixed address below. The switch may also be restarting.'),
    err.code === 'not_found' ? null : h('button', { class: 'btn primary', 'data-testid': 'retry', onclick: retry }, 'Try again'));
}
