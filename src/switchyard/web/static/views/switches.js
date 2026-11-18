import { get, post } from '../api.js';
import { clear, fmtAgo, h, isIPv4 } from '../util.js';
import { modal, run, toast } from '../ui.js';

function card(d) {
  const info = d.info || {};
  const title = d.label || info.name || info.model || d.mac;
  return h(
    'a',
    { class: 'card switch-card', href: `#/switch/${encodeURIComponent(d.mac)}`, 'data-testid': 'switch-card', 'data-mac': d.mac },
    h('h3', {}, h('span', { class: `dot ${d.online ? 'online' : 'offline'}`, title: d.online ? 'Online' : 'Not seen recently' }), title),
    h(
      'dl',
      { class: 'kv' },
      h('dt', {}, 'Model'), h('dd', {}, info.model || '?'),
      h('dt', {}, 'IP'), h('dd', {}, info.ip || '?'),
      h('dt', {}, 'MAC'), h('dd', { class: 'mono' }, d.mac),
      h('dt', {}, 'Firmware'), h('dd', {}, info.firmware || '?'),
      h('dt', {}, 'Seen'), h('dd', {}, d.online ? 'online' : fmtAgo(d.last_seen)),
    ),
    d.ports ? h('div', { class: 'minipanel', 'data-testid': 'minipanel', 'aria-label': 'Port state' }, ...d.ports.map((p) => h('span', { class: p.up ? `s${Math.min(p.speed_mbps || 10, 10000)}` : '', title: `Port ${p.port}: ${p.up ? `${p.speed_mbps} Mbit/s` : 'down'}` }))) : null,
    h('div', { class: 'row' }, h('span', { class: 'tag' }, d.has_password ? 'password stored' : 'no password'), d.address ? h('span', { class: 'tag' }, `unicast ${d.address}`) : null),
  );
}

function addByIp(done) {
  const ip = h('input', { type: 'text', id: 'add-ip', placeholder: '192.168.0.239', inputmode: 'decimal', 'data-testid': 'add-ip' });
  const label = h('input', { type: 'text', id: 'add-label', maxlength: '60' });
  const err = h('p', { class: 'error', role: 'alert' });
  modal({
    title: 'Add a switch by IP address',
    body: h('div', {},
      h('p', { class: 'muted' }, 'Use this for switches that broadcast discovery cannot reach, for example on another subnet.'),
      h('label', { for: 'add-ip' }, 'IP address'), ip,
      h('label', { for: 'add-label' }, 'Label (optional)'), label, err),
    actions: (close) => [
      h('button', { class: 'btn', onclick: close }, 'Cancel'),
      h('button', { class: 'btn primary', 'data-testid': 'add-ip-ok', onclick: async (e) => {
        if (!isIPv4(ip.value)) { err.textContent = 'Enter a valid IPv4 address.'; return; }
        e.target.disabled = true;
        try {
          await post('/devices', { address: ip.value.trim(), label: label.value.trim() });
          close();
          toast('Switch added.');
          done();
        } catch (ex) { err.textContent = ex.message; e.target.disabled = false; }
      } }, 'Add'),
    ],
  });
}

export async function renderSwitches(main, ctx) {
  let devices = [];
  let found = [];
  let scanning = false;
  const summary = h('div', { class: 'summary', 'data-testid': 'summary' });
  let eventCount = 0;
  const managed = h('div', { class: 'grid', 'data-testid': 'managed' });
  const foundBox = h('div', { 'data-testid': 'found' });
  const head = h('div', { class: 'page-head' }, h('h1', {}, 'Switches'));
  const scanBtn = h('button', { class: 'btn primary', 'data-testid': 'scan' }, 'Search network');
  const ipBtn = h('button', { class: 'btn', 'data-testid': 'add-by-ip' }, 'Add by IP address');
  head.append(ipBtn, scanBtn);
  main.append(head, summary, managed, foundBox);

  function drawSummary() {
    clear(summary);
    if (!devices.length) return;
    const online = devices.filter((d) => d.online).length;
    const withPorts = devices.filter((d) => d.ports);
    const up = withPorts.reduce((n, d) => n + d.ports.filter((p) => p.up).length, 0);
    const total = withPorts.reduce((n, d) => n + d.ports.length, 0);
    for (const [value, label, testid] of [[`${online} / ${devices.length}`, 'switches online', 'sum-online'], [total ? `${up} / ${total}` : '–', 'ports up', 'sum-ports'], [String(eventCount), 'events in 24 h', 'sum-events']]) {
      summary.append(h('div', { class: 'tile', 'data-testid': testid }, h('b', {}, value), h('span', { class: 'muted' }, label)));
    }
  }

  function drawManaged() {
    drawSummary();
    clear(managed);
    if (!devices.length) {
      managed.className = '';
      managed.append(h('div', { class: 'card empty', 'data-testid': 'empty' },
        h('h2', {}, 'No switches yet'),
        h('p', {}, 'Press ', h('b', {}, 'Search network'), ' to find switches on the same network segment, or add one by IP address.'),
        h('p', { class: 'muted' }, 'If nothing shows up:'),
        h('ul', { class: 'muted' },
          h('li', {}, 'The server and the switch must share a layer-2 network: discovery uses UDP broadcast.'),
          h('li', {}, 'In Docker, use host networking (network_mode: host).'),
          h('li', {}, 'Allow UDP ports 63321 to 63324 in your firewall.'),
          h('li', {}, 'Some newer firmware turns the Plus Utility protocol off. Enable it in the switch web interface.'))));
      return;
    }
    managed.className = 'grid';
    for (const d of devices) managed.append(card(d));
  }

  function drawFound() {
    clear(foundBox);
    const fresh = found.filter((f) => !f.managed);
    if (!scanning && !fresh.length && !found.length) return;
    const body = h('div', { class: 'card' }, h('h3', {}, 'Found on the network'));
    if (!fresh.length) body.append(h('p', { class: 'muted', 'data-testid': 'found-none' }, found.length ? 'Every switch that answered is already in your list.' : 'No switch answered.'));
    else {
      const rows = fresh.map((f) => h('tr', { 'data-testid': 'found-row', 'data-mac': f.mac },
        h('td', {}, f.name || '–'), h('td', {}, f.model || '?'), h('td', {}, f.ip || '?'), h('td', { class: 'mono' }, f.mac), h('td', {}, f.firmware || '?'),
        h('td', {}, h('button', { class: 'btn small primary', 'data-testid': 'add-found', onclick: async (e) => {
          await run(e.target, async () => { await post('/devices', { mac: f.mac }); toast(`Added ${f.model || 'switch'}.`); await refresh(); });
        } }, 'Add'))));
      body.append(h('div', { class: 'table-wrap' }, h('table', {},
        h('thead', {}, h('tr', {}, ...['Name', 'Model', 'IP', 'MAC', 'Firmware', ''].map((t) => h('th', {}, t)))), h('tbody', {}, rows))));
    }
    foundBox.append(body);
  }

  async function refresh() {
    devices = (await get('/devices')).devices;
    found = (await get('/discovery')).found;
    try {
      const since = Date.now() / 1000 - 86400;
      eventCount = (await get('/events')).events.filter((e) => e.time > since).length;
    } catch (err) { eventCount = 0; }
    drawManaged();
    drawFound();
  }

  scanBtn.addEventListener('click', async () => {
    scanning = true;
    scanBtn.textContent = 'Searching…';
    await run(scanBtn, async () => { found = (await post('/discovery/scan')).found; devices = (await get('/devices')).devices; });
    scanning = false;
    scanBtn.textContent = 'Search network';
    drawManaged();
    drawFound();
  });
  ipBtn.addEventListener('click', () => addByIp(refresh));
  await refresh();
  const timer = setInterval(async () => {
    if (document.hidden || document.querySelector('.overlay')) return;
    try { devices = (await get('/devices')).devices; drawManaged(); } catch (err) { /* next time */ }
  }, 8000);
  ctx.onCleanup(() => clearInterval(timer));
}
