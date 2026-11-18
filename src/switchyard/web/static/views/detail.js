// One switch: loads its state and hosts the tabs.
import { dev, get } from '../api.js';
import { clear, h } from '../util.js';
import { showError } from '../ui.js';
import { TABS } from './tabs.js';

export async function renderDetail(main, ctx, route) {
  const mac = route.mac;
  const tab = TABS.find((t) => t.id === route.tab) || TABS[0];
  const host = h('div', { 'data-testid': 'tab-body' });
  const header = h('div', { class: 'page-head' });
  const tabs = h('nav', { class: 'tabs', 'aria-label': 'Switch sections' },
    ...TABS.map((t) => h('a', { href: `#/switch/${encodeURIComponent(mac)}/${t.id}`, 'aria-current': t.id === tab.id ? 'page' : null, 'data-testid': `tab-${t.id}` }, t.label)));
  main.append(h('p', {}, h('a', { href: '#/' }, '← All switches')), header, tabs, host);

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
        host.append(unreachable(err, () => { clear(host); S.reload().then(() => S.show()).catch((e) => { clear(host); host.append(unreachable(e, () => ctx.navigate(`#/switch/${encodeURIComponent(mac)}/${tab.id}`))); }); }));
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
    host.append(unreachable(err, () => ctx.navigate(`#/switch/${encodeURIComponent(mac)}/${tab.id}`)));
    if (err.code !== 'not_found') showError(err);
    return;
  }
  S.retitle = title;
  await S.show();
}

function unreachable(err, retry) {
  return h('div', { class: 'card empty', 'data-testid': 'unreachable' },
    h('h2', {}, err.code === 'not_found' ? 'Unknown switch' : 'Switch not reachable'),
    h('p', {}, err.message),
    err.code === 'not_found' ? null : h('p', { class: 'muted' }, 'Check power, cabling and that the server shares the switch\'s network segment. The switch may also be restarting.'),
    err.code === 'not_found' ? null : h('button', { class: 'btn primary', 'data-testid': 'retry', onclick: retry }, 'Try again'));
}
