// The sections of the switch detail page.
import { ApiError, del, dev, get, patch, post, put } from '../api.js';
import { clear, download, fmtBytes, fmtLimit, fmtRate, h, portList, validateDot1q, validateNetwork } from '../util.js';
import { drawChart } from './chart.js';
import { confirmDialog, field, modal, run, section, select, showError, toast, unsupported, withSwitchAuth } from '../ui.js';

const portName = (S, p) => (S.state.device.port_labels || {})[p] || '';
const vlanName = (S, id) => (S.state.device.vlan_names || {})[id] || '';
const speedClass = (p) => (p.up ? `s${Math.min(p.speed_mbps || 10, 10000)}` : 'down');

function portPanel(ports, labels = {}) {
  const wrap = h('div', {}, h('div', { class: 'ports', 'data-testid': 'port-panel' },
    ...ports.map((p) => h('div', { class: `port ${p.up ? 'up' : 'down'} ${speedClass(p)}`, title: `Port ${p.port}${labels[p.port] ? ` (${labels[p.port]})` : ''}: ${p.link || 'unknown'}`, 'data-testid': `port-${p.port}` },
      h('div', { class: 'jack' }, String(p.port)), h('span', {}, p.up ? `${p.speed_mbps >= 1000 ? p.speed_mbps / 1000 + 'G' : p.speed_mbps + 'M'}` : 'down'), labels[p.port] ? h('small', { class: 'portlabel', 'data-testid': `portlabel-${p.port}` }, labels[p.port]) : null))),
    h('div', { class: 'legend' }, ...[['s10', '10 Mbit/s'], ['s100', '100 Mbit/s'], ['s1000', '1 Gbit/s'], ['s10000', '10 Gbit/s']].map(([c, t]) =>
      h('span', {}, h('i', { class: c }), t))));
  return wrap;
}

// Runs a change with the password prompt and refreshes afterwards.
function applier(S) {
  return (button, fn, message) => run(button, async () => {
    const result = await withSwitchAuth(S.mac, S.name, fn);
    toast((result && result.message) || message || 'Saved.');
    await S.reload();
    S.retitle();
    await S.show();
    return result;
  });
}

function poll(S, fn, ms) {
  let stopped = false;
  let timer = null;
  const tick = async () => {
    if (stopped) return;
    if (!document.hidden) await fn().catch(() => {});
    if (!stopped) timer = setTimeout(tick, ms);
  };
  timer = setTimeout(tick, ms);
  S.ctx.onCleanup(() => { stopped = true; clearTimeout(timer); });
}

// ---------------------------------------------------------------- overview
async function overview(host, S) {
  const { info } = S.state;
  const panel = h('div', { 'data-testid': 'ports-box' }, portPanel(S.state.ports, S.state.device.port_labels));
  const note = h('p', { class: 'error', 'data-testid': 'ports-error', hidden: true }, 'The switch is not answering. Trying again…');
  host.append(
    section('Ports', panel, note),
    section('Identity', h('dl', { class: 'kv', 'data-testid': 'identity' },
      ...[['Model', info.model], ['Name', info.name || '–'], ['MAC address', info.mac], ['Serial number', info.serial || '–'],
        ['Firmware', info.firmware || '–'], ['IP address', `${info.ip || '?'} ${info.dhcp ? '(DHCP)' : '(static)'}`],
        ['Subnet mask', info.netmask || '–'], ['Gateway', info.gateway || '–'], ['Ports', info.ports], ['Password scheme', info.password_mode]]
        .flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))]))),
  );
  poll(S, async () => {
    try {
      const { ports } = await get(`${dev(S.mac)}/ports`);
      clear(panel).append(portPanel(ports, S.state.device.port_labels));
      note.hidden = true;
    } catch (err) {
      note.hidden = false;
      throw err;
    }
  }, 5000);
}

// ----------------------------------------------------------------- network
async function network(host, S) {
  const { info } = S.state;
  const apply = applier(S);
  const name = h('input', { type: 'text', maxlength: '20', value: info.name, 'data-testid': 'net-name' });
  const dhcp = h('input', { type: 'checkbox', checked: info.dhcp, 'data-testid': 'net-dhcp' });
  const ip = h('input', { type: 'text', value: info.ip || '', 'data-testid': 'net-ip' });
  const mask = h('input', { type: 'text', value: info.netmask || '', 'data-testid': 'net-mask' });
  const gw = h('input', { type: 'text', value: info.gateway || '', 'data-testid': 'net-gateway' });
  const errors = h('div', { class: 'error', role: 'alert', 'data-testid': 'net-errors' });
  const save = h('button', { class: 'btn primary', 'data-testid': 'net-save' }, 'Save');
  const sync = () => { for (const el of [ip, mask, gw]) el.disabled = dhcp.checked; };
  dhcp.addEventListener('change', sync);
  sync();
  save.addEventListener('click', async () => {
    const values = { dhcp: dhcp.checked, ip: ip.value.trim(), netmask: mask.value.trim(), gateway: gw.value.trim() };
    const problems = validateNetwork(values);
    clear(errors).append(...problems.map((p) => h('div', {}, p)));
    if (problems.length) return;
    const changesIp = values.dhcp !== info.dhcp || (!values.dhcp && values.ip !== info.ip);
    if (changesIp && !(await confirmDialog({ title: 'Change IP settings', message: 'The switch may be unreachable for a moment after this. If the new address is on another subnet you must reach it from there.', confirmLabel: 'Apply' }))) return;
    const body = { name: name.value.trim() };
    if (changesIp || values.dhcp !== info.dhcp) Object.assign(body, values.dhcp ? { dhcp: true } : { dhcp: false, ip: values.ip, netmask: values.netmask, gateway: values.gateway || '0.0.0.0' });
    await apply(save, () => put(`${dev(S.mac)}/network`, body), 'Settings saved.');
  });
  host.append(section('Name and IP settings',
    field('Switch name', name, 'Up to 20 characters.'),
    h('label', { class: 'check' }, dhcp, ' Get the address from DHCP'),
    field('IP address', ip), field('Subnet mask', mask), field('Gateway', gw, 'Empty for none.'),
    errors, h('p', {}, save)));
}

// ------------------------------------------------------------------- ports
async function ports(host, S) {
  const body = h('tbody', { 'data-testid': 'stats-body' });
  let previous = null;
  const draw = (rows, t) => {
    clear(body);
    for (const p of rows) {
      const before = previous && previous.rows.get(p.port);
      const dt = previous ? (t - previous.t) / 1000 : 0;
      const rate = (a, b) => (before && dt > 0 && a >= b ? (a - b) / dt : null);
      body.append(h('tr', { 'data-testid': `stat-${p.port}` },
        h('td', {}, String(p.port), portName(S, p.port) ? h('small', { class: 'muted' }, ` ${portName(S, p.port)}`) : null), h('td', {}, h('span', { class: `dot ${p.up ? 'online' : ''}` }), ' ', p.link || '–'),
        h('td', {}, p.duplex || '–'),
        h('td', { class: 'num', title: `${p.rx_bytes} bytes` }, fmtBytes(p.rx_bytes)),
        h('td', { class: 'num' }, fmtRate(rate(p.rx_bytes, before && before.rx_bytes))),
        h('td', { class: 'num', title: `${p.tx_bytes} bytes` }, fmtBytes(p.tx_bytes)),
        h('td', { class: 'num' }, fmtRate(rate(p.tx_bytes, before && before.tx_bytes))),
        h('td', { class: 'num' }, String(p.crc_errors ?? 0))));
    }
    previous = { t, rows: new Map(rows.map((p) => [p.port, p])) };
  };
  draw(S.state.ports, Date.now());
  const clearBtn = h('button', { class: 'btn', 'data-testid': 'clear-counters' }, 'Clear counters');
  clearBtn.addEventListener('click', () => applier(S)(clearBtn, () => post(`${dev(S.mac)}/stats/reset`), 'Counters cleared.'));
  host.append(section('Port status and statistics',
    h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ...['Port', 'Link', 'Duplex', 'Received', 'Rx rate', 'Sent', 'Tx rate', 'CRC errors'].map((t, i) => h('th', { class: i > 2 ? 'num' : '' }, t)))), body)),
    h('p', {}, clearBtn)));
  poll(S, async () => draw((await get(`${dev(S.mac)}/ports`)).ports, Date.now()), 3000);

  // traffic history (needs the background monitor)
  const chartBox = h('div', { 'data-testid': 'chart-box' });
  const which = select([[0, 'All ports'], ...S.state.ports.map((p) => [p.port, `Port ${p.port}`])], 0, { 'aria-label': 'Port for the chart', 'data-testid': 'chart-port' });
  const range = select([[15, 'Last 15 minutes'], [60, 'Last hour']], 60, { 'aria-label': 'Time range', 'data-testid': 'chart-range' });
  const drawHistory = async () => {
    const hist = await get(`${dev(S.mac)}/history?minutes=${range.value}`);
    const points = hist.series[which.value] || [];
    clear(chartBox);
    if (points.length < 2) {
      chartBox.append(h('p', { class: 'muted', 'data-testid': 'chart-empty' }, hist.interval ? `Collecting data… the monitor reads the ports every ${hist.interval} s.` : 'The monitor is off. Set monitor.interval_seconds to record traffic history.'));
      return;
    }
    chartBox.append(drawChart(points), h('div', { class: 'chart-legend' }, h('span', {}, h('i', { class: 'rx' }), 'Received'), h('span', {}, h('i', { class: 'tx' }), 'Sent')));
  };
  which.addEventListener('change', () => drawHistory().catch(showError));
  range.addEventListener('change', () => drawHistory().catch(showError));
  host.append(section('Traffic history', h('div', { class: 'row' }, which, range), chartBox));
  await drawHistory().catch(() => {});
  poll(S, drawHistory, 10000);
}

// -------------------------------------------------------------------- vlan
const MODE_LABELS = { disabled: 'Disabled', port_basic: 'Port-based, basic', port_advanced: 'Port-based, advanced', dot1q_basic: '802.1Q, basic', dot1q_advanced: '802.1Q, advanced' };

async function vlan(host, S) {
  const v = S.state.vlan;
  if (!v) return host.append(section('VLAN', unsupported('VLAN configuration')));
  const apply = applier(S);
  const n = S.state.info.ports;
  const allPorts = Array.from({ length: n }, (_, i) => i + 1);
  const modeSel = select(Object.entries(MODE_LABELS), v.mode, { 'data-testid': 'vlan-mode' });
  const modeBtn = h('button', { class: 'btn', 'data-testid': 'vlan-mode-apply' }, 'Change mode');
  modeBtn.addEventListener('click', async () => {
    if (modeSel.value === v.mode) return;
    if (!(await confirmDialog({ title: 'Change VLAN mode', message: 'Changing the VLAN mode erases all current VLAN settings on the switch.', confirmLabel: 'Change mode', danger: true }))) return;
    await apply(modeBtn, () => put(`${dev(S.mac)}/vlan/mode`, { mode: modeSel.value }), 'VLAN mode changed.');
  });
  host.append(section('VLAN mode', h('div', { class: 'row' }, modeSel, modeBtn),
    h('p', { class: 'muted' }, 'Basic modes are simpler on the switch itself; advanced modes allow overlapping groups and tagging.')));
  if (v.mode === 'disabled') return host.append(section('VLANs', h('p', { class: 'muted' }, 'VLANs are disabled. Pick a mode above to start.')));

  const dirtyNote = h('span', { class: 'dirty', 'data-testid': 'dirty', hidden: true }, 'Unsaved changes');
  const errBox = h('div', { class: 'error', role: 'alert', 'data-testid': 'vlan-errors' });
  const matrixBox = h('div', { class: 'table-wrap', 'data-testid': 'vlan-matrix' });
  const applyBtn = h('button', { class: 'btn primary', 'data-testid': 'vlan-apply' }, 'Apply');
  const discard = h('button', { class: 'btn', 'data-testid': 'vlan-discard' }, 'Discard changes');
  const head = (extra) => h('thead', {}, h('tr', {}, h('th', {}, 'VLAN'), ...allPorts.map((p) => h('th', {}, String(p))), extra));

  if (v.mode.startsWith('port')) {
    const original = v.port_vlans.map((g) => ({ id: g.id, members: new Set(g.members) }));
    let model = original.map((g) => ({ id: g.id, members: new Set(g.members) }));
    const snapshot = (m) => JSON.stringify(m.map((g) => [g.id, [...g.members].sort()]));
    const draw = () => {
      const dirty = snapshot(model) !== snapshot(original);
      dirtyNote.hidden = !dirty; applyBtn.disabled = !dirty;
      S.ctx.setDirty(() => dirty);
      clear(matrixBox).append(h('table', { class: 'matrix' }, head(null), h('tbody', {}, ...model.map((g) => h('tr', { 'data-testid': `vlan-row-${g.id}` },
        h('td', {}, `Group ${g.id}`), ...allPorts.map((p) => h('td', {}, h('button', { class: `cell ${g.members.has(p) ? 'M' : ''}`, 'aria-pressed': String(g.members.has(p)), 'aria-label': `Group ${g.id}, port ${p}`, 'data-testid': `cell-${g.id}-${p}`,
          onclick: () => { if (g.members.has(p)) g.members.delete(p); else g.members.add(p); draw(); } }, g.members.has(p) ? '✓' : '–'))))))));
    };
    draw();
    applyBtn.addEventListener('click', () => apply(applyBtn, () => put(`${dev(S.mac)}/vlan/port-based`, { vlans: model.map((g) => ({ id: g.id, members: [...g.members].sort((a, b) => a - b) })) }), 'VLANs saved.'));
    discard.addEventListener('click', () => { model = original.map((g) => ({ id: g.id, members: new Set(g.members) })); draw(); });
    return host.append(section('Port-based VLAN groups', matrixBox, h('div', { class: 'row' }, applyBtn, discard, dirtyNote)));
  }

  // 802.1Q
  const fromServer = () => ({
    vlans: v.dot1q.map((x) => ({ id: x.id, members: new Set(x.members), tagged: new Set(x.tagged) })),
    pvid: Object.fromEntries(allPorts.map((p) => [p, Number(v.pvid[p] ?? 1)])),
  });
  let { vlans, pvid } = fromServer();
  const original = JSON.stringify(snapshot802(fromServer()));
  const newId = h('input', { type: 'number', min: '1', max: '4093', 'aria-label': 'New VLAN ID', 'data-testid': 'new-vlan-id', style: null });
  newId.style.width = '110px';
  const addBtn = h('button', { class: 'btn', 'data-testid': 'add-vlan' }, 'Add VLAN');
  const draw = () => {
    vlans.sort((a, b) => a.id - b.id);
    const errors = validateDot1q(vlans, pvid, n, v.max_vlans);
    const dirty = JSON.stringify(snapshot802({ vlans, pvid })) !== original;
    dirtyNote.hidden = !dirty; applyBtn.disabled = !dirty || errors.length > 0;
    S.ctx.setDirty(() => dirty);
    clear(errBox).append(...errors.map((e) => h('div', {}, e)));
    const cellOf = (vl, p) => {
      const state = !vl.members.has(p) ? '' : vl.tagged.has(p) ? 'T' : 'U';
      const words = { '': 'not a member', U: 'untagged', T: 'tagged' }[state];
      return h('button', { class: `cell ${state}`, 'aria-label': `VLAN ${vl.id}, port ${p}: ${words}`, 'data-testid': `cell-${vl.id}-${p}`, 'data-state': state || '-',
        onclick: () => {
          if (!state) vl.members.add(p);
          else if (state === 'U') vl.tagged.add(p);
          else { vl.members.delete(p); vl.tagged.delete(p); }
          draw();
        } }, state || '–');
    };
    const pvidCell = (p) => {
      const options = vlans.filter((x) => x.members.has(p)).map((x) => [x.id, String(x.id)]);
      if (!options.some(([id]) => Number(id) === pvid[p])) options.unshift([pvid[p], `${pvid[p]} !`]);
      const sel = select(options, pvid[p], { 'aria-label': `PVID of port ${p}`, 'data-testid': `pvid-${p}` });
      sel.addEventListener('change', () => { pvid[p] = Number(sel.value); draw(); });
      return h('td', {}, sel);
    };
    clear(matrixBox).append(h('table', { class: 'matrix' }, head(h('th', {}, '')),
      h('tbody', {}, ...vlans.map((vl) => h('tr', { 'data-testid': `vlan-row-${vl.id}` }, h('td', {}, `VLAN ${vl.id}`, vlanName(S, vl.id) ? h('small', { class: 'muted' }, ` ${vlanName(S, vl.id)}`) : null), ...allPorts.map((p) => h('td', {}, cellOf(vl, p))),
        h('td', {}, vl.id === 1 ? null : h('button', { class: 'btn small ghost', 'aria-label': `Remove VLAN ${vl.id}`, 'data-testid': `remove-vlan-${vl.id}`,
          onclick: () => { vlans = vlans.filter((x) => x !== vl); for (const p of allPorts) if (pvid[p] === vl.id) pvid[p] = 1; draw(); } }, 'Remove')))),
        h('tr', { class: 'pvid-row' }, h('td', {}, h('b', {}, 'PVID')), ...allPorts.map(pvidCell), h('td', {})))));
  };
  addBtn.addEventListener('click', () => {
    const id = Number(newId.value);
    if (!Number.isInteger(id) || id < 1 || id > 4093) return toast('VLAN ID must be 1 to 4093.', 'error');
    if (vlans.some((x) => x.id === id)) return toast(`VLAN ${id} exists already.`, 'error');
    vlans.push({ id, members: new Set(), tagged: new Set() });
    newId.value = '';
    draw();
  });
  applyBtn.addEventListener('click', () => apply(applyBtn, () => put(`${dev(S.mac)}/vlan/dot1q`, {
    vlans: vlans.map((x) => ({ id: x.id, members: [...x.members].sort((a, b) => a - b), tagged: [...x.tagged].sort((a, b) => a - b) })), pvid }), 'VLANs saved.'));
  discard.addEventListener('click', () => { ({ vlans, pvid } = fromServer()); draw(); });
  draw();
  host.append(
    section('802.1Q VLANs', h('p', { class: 'muted' }, 'Click a cell to cycle: – not a member, U untagged, T tagged. The PVID row sets the VLAN for untagged incoming traffic; a port must be a member of its PVID VLAN.'),
      matrixBox, h('div', { class: 'row' }, h('span', {}, 'New VLAN'), newId, addBtn), errBox,
      h('div', { class: 'row' }, applyBtn, discard, dirtyNote)),
    h('div', { class: 'notice' }, 'A wrong VLAN setup can cut you off from the switch. If that happens, press the reset button on the switch to restore factory settings. ',
      `This switch holds up to ${v.max_vlans || '?'} VLANs.`));
}

function snapshot802({ vlans, pvid }) {
  return [vlans.map((x) => [x.id, [...x.members].sort((a, b) => a - b), [...x.tagged].sort((a, b) => a - b)]).sort((a, b) => a[0] - b[0]), Object.entries(pvid).map(([k, x]) => [Number(k), Number(x)])];
}

// --------------------------------------------------------------------- qos
async function qos(host, S) {
  const q = S.state.qos;
  if (!q) return host.append(section('Quality of service', unsupported('QoS')));
  const apply = applier(S);
  const n = S.state.info.ports;
  const allPorts = Array.from({ length: n }, (_, i) => i + 1);
  const rates = Object.entries(S.state.rate_codes).map(([code, kbit]) => [code, fmtLimit(kbit)]);
  const mode = select([['dot1p', '802.1p based'], ['port', 'Port based']], q.mode, { 'data-testid': 'qos-mode' });
  const prio = {}; const ingress = {}; const egress = {}; const storm = {};
  const table = h('table', {}, h('thead', {}, h('tr', {}, ...['Port', 'Priority', 'Ingress limit', 'Egress limit', 'Storm control rate'].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...allPorts.map((p) => {
      prio[p] = select(['high', 'medium', 'normal', 'low'].map((x) => [x, x[0].toUpperCase() + x.slice(1)]), q.priorities[p] || 'normal', { 'aria-label': `Priority of port ${p}`, 'data-testid': `prio-${p}` });
      ingress[p] = select(rates, q.ingress[p] ?? 0, { 'aria-label': `Ingress limit of port ${p}`, 'data-testid': `ingress-${p}` });
      egress[p] = select(rates, q.egress[p] ?? 0, { 'aria-label': `Egress limit of port ${p}`, 'data-testid': `egress-${p}` });
      storm[p] = select(rates, q.storm_rates[p] ?? 0, { 'aria-label': `Storm control rate of port ${p}`, 'data-testid': `storm-${p}` });
      return h('tr', {}, h('td', {}, String(p)), h('td', {}, prio[p]), h('td', {}, ingress[p]), h('td', {}, egress[p]), h('td', {}, storm[p]));
    })));
  const stormOn = h('input', { type: 'checkbox', checked: Boolean(q.storm_control), disabled: q.storm_control === null, 'data-testid': 'storm-enabled' });
  const sync = () => { for (const p of allPorts) { prio[p].disabled = mode.value !== 'port'; storm[p].disabled = !stormOn.checked; } };
  mode.addEventListener('change', sync); stormOn.addEventListener('change', sync); sync();
  const save = h('button', { class: 'btn primary', 'data-testid': 'qos-save' }, 'Apply');
  save.addEventListener('click', async () => {
    const body = { ingress: {}, egress: {} };
    if (mode.value !== q.mode) {
      if (!(await confirmDialog({ title: 'Change QoS mode', message: 'Changing the QoS mode erases the current priority settings.', confirmLabel: 'Change mode', danger: true }))) return;
      body.mode = mode.value;
    }
    if (mode.value === 'port') body.priorities = Object.fromEntries(allPorts.map((p) => [p, prio[p].value]));
    for (const p of allPorts) { body.ingress[p] = Number(ingress[p].value); body.egress[p] = Number(egress[p].value); }
    if (q.storm_control !== null) {
      body.storm_control = stormOn.checked;
      if (stormOn.checked) body.storm_rates = Object.fromEntries(allPorts.map((p) => [p, Number(storm[p].value)]));
    }
    await apply(save, () => put(`${dev(S.mac)}/qos`, body), 'QoS settings saved.');
  });
  host.append(section('Quality of service', field('QoS mode', mode, 'Port priorities can be set in port-based mode only.'),
    h('label', { class: 'check' }, stormOn, ' Broadcast storm control'), h('div', { class: 'table-wrap' }, table), h('p', {}, save)));
}

// ---------------------------------------------------------------- features
async function features(host, S) {
  const apply = applier(S);
  const n = S.state.info.ports;
  const allPorts = Array.from({ length: n }, (_, i) => i + 1);

  const m = S.state.mirror;
  if (!m) host.append(section('Port mirroring', unsupported('Port mirroring')));
  else {
    const dest = select([[0, 'Off'], ...allPorts.map((p) => [p, `Port ${p}`])], m.destination, { 'data-testid': 'mirror-dest' });
    const boxes = allPorts.map((p) => h('input', { type: 'checkbox', checked: m.sources.includes(p), 'data-testid': `mirror-src-${p}` }));
    const save = h('button', { class: 'btn primary', 'data-testid': 'mirror-save' }, 'Apply');
    save.addEventListener('click', () => {
      const sources = allPorts.filter((p, i) => boxes[i].checked);
      const d = Number(dest.value);
      if (d && sources.includes(d)) return toast('The destination port cannot also be a source port.', 'error');
      if (d && !sources.length) return toast('Select at least one source port.', 'error');
      return apply(save, () => put(`${dev(S.mac)}/mirror`, { destination: d, sources: d ? sources : [] }), 'Mirroring saved.');
    });
    host.append(section('Port mirroring', field('Destination port', dest, 'Copies of the traffic of the source ports are sent here.'),
      h('div', { class: 'checks' }, ...allPorts.map((p, i) => h('label', {}, boxes[i], `Port ${p}`))), h('p', {}, save)));
  }

  const g = S.state.igmp;
  if (!g) host.append(section('IGMP snooping', unsupported('IGMP snooping')));
  else {
    const on = h('input', { type: 'checkbox', checked: g.enabled, 'data-testid': 'igmp-enabled' });
    const vid = h('input', { type: 'number', min: '1', max: '4093', value: g.vlan || 1, 'data-testid': 'igmp-vlan' });
    const block = h('input', { type: 'checkbox', checked: Boolean(g.block_unknown), disabled: g.block_unknown === null, 'data-testid': 'igmp-block' });
    const validate = h('input', { type: 'checkbox', checked: Boolean(g.validate_v3), disabled: g.validate_v3 === null, 'data-testid': 'igmp-validate' });
    const save = h('button', { class: 'btn primary', 'data-testid': 'igmp-save' }, 'Apply');
    save.addEventListener('click', () => {
      const id = Number(vid.value);
      if (on.checked && (!Number.isInteger(id) || id < 1 || id > 4093)) return toast('VLAN ID must be 1 to 4093.', 'error');
      return apply(save, () => put(`${dev(S.mac)}/igmp`, { enabled: on.checked, vlan: id || 1, block_unknown: g.block_unknown === null ? null : block.checked, validate_v3: g.validate_v3 === null ? null : validate.checked }), 'IGMP snooping saved.');
    });
    host.append(section('IGMP snooping', h('label', { class: 'check' }, on, ' Enable IGMP snooping'), field('VLAN ID', vid),
      h('label', { class: 'check' }, block, ' Block unknown multicast addresses'), h('label', { class: 'check' }, validate, ' Validate IGMPv3 IP header'), h('p', {}, save)));
  }

  const loop = S.state.loop_detection;
  if (loop === null) host.append(section('Loop detection', unsupported('Loop detection')));
  else {
    const on = h('input', { type: 'checkbox', checked: loop, 'data-testid': 'loop-enabled' });
    const save = h('button', { class: 'btn primary', 'data-testid': 'loop-save' }, 'Apply');
    save.addEventListener('click', () => apply(save, () => put(`${dev(S.mac)}/loop-detection`, { enabled: on.checked }), 'Loop detection saved.'));
    host.append(section('Loop detection', h('label', { class: 'check' }, on, ' Detect network loops and block looping ports'), h('p', {}, save)));
  }
}

// ------------------------------------------------------------------- cable
async function cable(host, S) {
  const n = S.state.info.ports;
  const allPorts = Array.from({ length: n }, (_, i) => i + 1);
  const boxes = allPorts.map((p) => h('input', { type: 'checkbox', 'data-testid': `cable-port-${p}` }));
  const results = h('div', { 'data-testid': 'cable-results' });
  const run_ = h('button', { class: 'btn primary', 'data-testid': 'cable-run' }, 'Run test');
  const labels = { ok: 'OK', no_cable: 'No cable', open: 'Open cable', short: 'Short circuit', shorted: 'Shorted cable', fiber: 'Fiber cable', crosstalk: 'Crosstalk', unknown: 'Unknown' };
  run_.addEventListener('click', async () => {
    const selected = allPorts.filter((p, i) => boxes[i].checked);
    if (!selected.length) return toast('Select at least one port.', 'error');
    clear(results).append(h('p', { class: 'muted' }, 'Testing… the link on a tested port may drop briefly.'));
    const done = await run(run_, () => withSwitchAuth(S.mac, S.name, () => post(`${dev(S.mac)}/cable-test`, { ports: selected })));
    clear(results);
    if (done) results.append(h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Port'), h('th', {}, 'Result'), h('th', { class: 'num' }, 'Fault distance (m)'))),
      h('tbody', {}, ...done.results.map((r) => h('tr', { 'data-testid': `cable-${r.port}` }, h('td', {}, String(r.port)), h('td', {}, labels[r.status] || r.status), h('td', { class: 'num' }, r.status === 'ok' ? '–' : String(r.distance_m))))))));
  });
  host.append(section('Cable test', h('p', { class: 'muted' }, 'Checks the cable on a port for faults and estimates where a fault is.'),
    h('div', { class: 'checks' }, ...allPorts.map((p, i) => h('label', {}, boxes[i], `Port ${p}`))), h('p', {}, run_), results));
}


// ------------------------------------------------------------- port settings
async function portSettings(host, S) {
  const admin = S.state.port_admin;
  if (!admin) return host.append(section('Port settings', unsupported('Speed and flow control settings')));
  const apply = applier(S);
  const speeds = Object.entries(S.state.admin_speeds).map(([code, label]) => [code, label[0].toUpperCase() + label.slice(1)]);
  const live = Object.fromEntries(S.state.ports.map((p) => [p.port, p]));
  const speedSel = {};
  const flow = {};
  const rows = Object.keys(admin).map(Number).sort((a, b) => a - b).map((p) => {
    speedSel[p] = select(speeds, admin[p].speed_code, { 'aria-label': `Speed of port ${p}`, 'data-testid': `speed-${p}` });
    flow[p] = h('input', { type: 'checkbox', checked: admin[p].flow_control, 'aria-label': `Flow control of port ${p}`, 'data-testid': `flow-${p}` });
    return h('tr', {}, h('td', {}, String(p)), h('td', {}, live[p] ? live[p].link : '–'), h('td', {}, speedSel[p]), h('td', {}, flow[p]));
  });
  const save = h('button', { class: 'btn primary', 'data-testid': 'port-settings-save' }, 'Apply');
  save.addEventListener('click', async () => {
    const changed = {};
    for (const p of Object.keys(admin).map(Number)) {
      const code = Number(speedSel[p].value);
      if (code !== admin[p].speed_code || flow[p].checked !== admin[p].flow_control) changed[p] = { speed_code: code, flow_control: flow[p].checked };
    }
    if (!Object.keys(changed).length) return toast('Nothing changed.');
    const off = Object.keys(changed).filter((p) => changed[p].speed_code === 1);
    if (off.length && !(await confirmDialog({ title: 'Turn ports off', message: `Port ${off.join(', ')} will be switched off. If this server reaches the switch through such a port, you lose the connection.`, confirmLabel: 'Apply', danger: true }))) return;
    await apply(save, () => put(`${dev(S.mac)}/port-settings`, { ports: changed }), 'Port settings saved.');
  });
  host.append(section('Port speed and flow control',
    h('p', { class: 'muted' }, 'Auto negotiates. Fixed speeds need the same setting on the device at the other end. Members of a link aggregation group must share one setting.'),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ...['Port', 'Link now', 'Speed setting', 'Flow control'].map((t) => h('th', {}, t)))), h('tbody', {}, rows))),
    h('p', {}, save)));
}

// ----------------------------------------------------------------------- lag
async function lagTab(host, S) {
  const lag = S.state.lag;
  if (!lag) return host.append(section('Link aggregation', unsupported('Link aggregation')));
  const apply = applier(S);
  const n = S.state.info.ports;
  const allPorts = Array.from({ length: n }, (_, i) => i + 1);
  let model = lag.lags.map((g) => ({ id: g.id, enabled: g.enabled, members: new Set(g.members) }));
  const snapshot = (m) => JSON.stringify(m.map((g) => [g.id, g.enabled, [...g.members].sort((a, b) => a - b)]));
  const original = snapshot(model);
  const box = h('div', { 'data-testid': 'lag-matrix', class: 'table-wrap' });
  const errBox = h('div', { class: 'error', role: 'alert', 'data-testid': 'lag-errors' });
  const dirtyNote = h('span', { class: 'dirty', hidden: true }, 'Unsaved changes');
  const save = h('button', { class: 'btn primary', 'data-testid': 'lag-save' }, 'Apply');
  const discard = h('button', { class: 'btn', 'data-testid': 'lag-discard' }, 'Discard changes');
  const validate = () => {
    const errors = [];
    const used = new Map();
    for (const g of model.filter((x) => x.enabled)) {
      if (g.members.size < 2) errors.push(`LAG ${g.id} needs at least 2 member ports.`);
      if (g.members.size > lag.max_members) errors.push(`LAG ${g.id} has at most ${lag.max_members} member ports.`);
      for (const p of g.members) {
        if (used.has(p)) errors.push(`Port ${p} is in LAG ${used.get(p)} and LAG ${g.id}.`);
        used.set(p, g.id);
      }
    }
    return errors;
  };
  const draw = () => {
    const errors = validate();
    const dirty = snapshot(model) !== original;
    dirtyNote.hidden = !dirty;
    save.disabled = !dirty || errors.length > 0;
    S.ctx.setDirty(() => dirty);
    clear(errBox).append(...errors.map((e) => h('div', {}, e)));
    clear(box).append(h('table', { class: 'matrix' },
      h('thead', {}, h('tr', {}, h('th', {}, 'LAG'), h('th', {}, 'Active'), ...allPorts.map((p) => h('th', {}, String(p))))),
      h('tbody', {}, ...model.map((g) => h('tr', { 'data-testid': `lag-row-${g.id}` },
        h('td', {}, `LAG ${g.id}`),
        h('td', {}, h('input', { type: 'checkbox', checked: g.enabled, 'aria-label': `LAG ${g.id} active`, 'data-testid': `lag-on-${g.id}`, onchange: (e) => { g.enabled = e.target.checked; draw(); } })),
        ...allPorts.map((p) => h('td', {}, h('button', { class: `cell ${g.members.has(p) ? 'M' : ''}`, disabled: !g.enabled, 'aria-pressed': String(g.members.has(p)), 'aria-label': `LAG ${g.id}, port ${p}`, 'data-testid': `lag-cell-${g.id}-${p}`,
          onclick: () => { if (g.members.has(p)) g.members.delete(p); else g.members.add(p); draw(); } }, g.members.has(p) ? '✓' : '–'))))))));
  };
  save.addEventListener('click', () => apply(save, () => put(`${dev(S.mac)}/lag`, { lags: model.map((g) => ({ id: g.id, enabled: g.enabled, members: g.enabled ? [...g.members].sort((a, b) => a - b) : [] })) }), 'Link aggregation saved.'));
  discard.addEventListener('click', () => { model = lag.lags.map((g) => ({ id: g.id, enabled: g.enabled, members: new Set(g.members) })); draw(); });
  draw();
  host.append(section('Link aggregation (LAG)',
    h('p', { class: 'muted' }, `Static trunks that bundle ports into one link. Up to ${lag.groups} groups with at most ${lag.max_members} ports each. This is static aggregation: the device at the other end must be set up the same way (LACP is not supported by these switches).`),
    box, errBox, h('div', { class: 'row' }, save, discard, dirtyNote)),
    h('div', { class: 'notice' }, 'Ports in one group need the same speed, flow control, PVID and VLAN membership, and cannot take part in port mirroring. The switch refuses the change otherwise and says why.'));
}

// ------------------------------------------------------------------ firmware
async function firmware(host, S) {
  const apply = applier(S);
  const info = S.state.info;
  const file = h('input', { type: 'file', accept: '.flash,.bin,.img', 'aria-label': 'Firmware file', 'data-testid': 'fw-file' });
  const result = h('div', { 'data-testid': 'fw-check' });
  const progress = h('div', { 'data-testid': 'fw-progress' });
  const force = h('input', { type: 'checkbox', 'data-testid': 'fw-force' });
  const go = h('button', { class: 'btn danger', 'data-testid': 'fw-start', disabled: true }, 'Upgrade firmware');
  let checked = null;
  let timer = null;
  S.ctx.onCleanup(() => clearInterval(timer));

  const showStatus = (st) => {
    clear(progress);
    if (st.state === 'idle') return;
    const pct = st.total ? Math.round((st.sent / st.total) * 100) : 0;
    const label = { running: st.step, done: `Done. The switch now runs ${st.version}.`, failed: `Failed: ${st.error}` }[st.state];
    progress.append(h('div', { class: `notice ${st.state === 'failed' ? 'danger' : ''}`, 'data-testid': 'fw-state', 'data-state': st.state },
      h('b', {}, st.state === 'running' ? 'Upgrade running. Do not power off the switch or unplug it. ' : ''), label,
      st.state === 'running' && st.step === 'uploading' ? h('progress', { max: '100', value: String(pct), 'aria-label': 'Upload progress', style: null }, `${pct} %`) : null,
      st.state === 'running' && st.step === 'uploading' ? ` ${pct} %` : null));
  };
  const poll = async () => {
    try {
      const st = await get(`${dev(S.mac)}/firmware`);
      showStatus(st);
      if (st.state !== 'running') { clearInterval(timer); timer = null; go.disabled = !checked; if (st.state === 'done') { await S.reload().catch(() => {}); S.retitle(); } }
    } catch (err) { /* the switch restarts: ignore until it answers again */ }
  };
  const watch = () => { if (!timer) timer = setInterval(poll, 1500); };
  get(`${dev(S.mac)}/firmware`).then((st) => { showStatus(st); if (st.state === 'running') { go.disabled = true; watch(); } }).catch(() => {});

  file.addEventListener('change', async () => {
    checked = null; go.disabled = true; clear(result);
    const f = file.files && file.files[0];
    if (!f) return;
    try {
      const buf = await f.arrayBuffer();
      const r = await fetch(`/api${dev(S.mac)}/firmware/check`, { method: 'POST', credentials: 'same-origin', body: buf });
      const body = await r.json();
      if (!r.ok) throw new Error(body.message || 'The file was refused.');
      checked = { buf, name: f.name, info: body };
      const im = body.image;
      result.append(h('dl', { class: 'kv' },
        h('dt', {}, 'Image for'), h('dd', {}, im.board || '?'), h('dt', {}, 'Image version'), h('dd', {}, `${im.version} (${im.version_raw})`),
        h('dt', {}, 'Switch'), h('dd', {}, `${body.switch.model}, now ${body.switch.firmware}`), h('dt', {}, 'Size'), h('dd', {}, fmtBytes(im.size)), h('dt', {}, 'Checksum'), h('dd', {}, 'valid')));
      if (!body.matches) result.append(h('div', { class: 'notice danger' }, `This image is for ${im.board}, not for a ${body.switch.model}. Flashing it can ruin the switch.`));
      else if (body.same_version) result.append(h('div', { class: 'notice' }, 'The switch already runs this version.'));
      go.disabled = !body.matches && !force.checked;
    } catch (err) { result.append(h('p', { class: 'error' }, err.message)); }
  });
  force.addEventListener('change', () => { go.disabled = !checked || (!checked.info.matches && !force.checked); });
  go.addEventListener('click', async () => {
    if (!checked) return;
    const typed = await confirmDialog({ title: 'Upgrade firmware', message: 'The switch restarts and is offline for a few minutes. A power loss or a wrong image during flashing can make it unusable. Back up the configuration first. Continue only if the switch has stable power.', confirmLabel: 'Start upgrade', danger: true, typeToConfirm: S.mac });
    if (!typed) return;
    await run(go, () => withSwitchAuth(S.mac, S.name, async () => {
      const q = new URLSearchParams({ confirm: typed.toLowerCase(), force: String(force.checked), filename: checked.name });
      const r = await fetch(`/api${dev(S.mac)}/firmware?${q}`, { method: 'POST', credentials: 'same-origin', body: checked.buf });
      const body = await r.json();
      if (!r.ok) throw new ApiError(r.status, body);
      showStatus(body); watch();
    }));
  });
  host.append(
    section('Firmware', h('dl', { class: 'kv' }, h('dt', {}, 'Model'), h('dd', {}, info.model), h('dt', {}, 'Running'), h('dd', {}, info.firmware || '–'), info.firmware2 ? h('dt', {}, 'Second image') : null, info.firmware2 ? h('dd', {}, info.firmware2) : null)),
    section('Upgrade', h('div', { class: 'notice danger' }, 'A firmware upgrade can make a switch unusable if it is interrupted or if the image is wrong. Use only the official image for your exact model and hardware revision. Make a backup first.'),
      field('Firmware file (.flash)', file), result,
      h('label', { class: 'check' }, force, ' Allow an image whose board name does not match (dangerous)'), h('p', {}, go), progress));
}

// -------------------------------------------------------------- maintenance
async function maintenance(host, S) {
  const apply = applier(S);
  const d = S.state.device;
  const label = h('input', { type: 'text', value: d.label, maxlength: '60', 'data-testid': 'meta-label' });
  const notes = h('textarea', { rows: '3', maxlength: '2000', 'data-testid': 'meta-notes' }); notes.value = d.notes || '';
  const address = h('input', { type: 'text', value: d.address || '', placeholder: 'empty = find by broadcast', 'data-testid': 'meta-address' });
  const saveMeta = h('button', { class: 'btn primary', 'data-testid': 'meta-save' }, 'Save');
  saveMeta.addEventListener('click', () => run(saveMeta, async () => {
    await patch(dev(S.mac), { label: label.value, notes: notes.value, set_address: true, address: address.value.trim() || null });
    toast('Saved.'); await S.reload(); S.retitle();
  }));
  const forget = h('button', { class: 'btn', 'data-testid': 'forget-password', disabled: !d.remembered }, 'Forget stored password');
  forget.addEventListener('click', () => run(forget, async () => { await patch(dev(S.mac), { forget_password: true }); toast('Password forgotten.'); await S.reload(); await S.show(); }));
  const remove = h('button', { class: 'btn danger', 'data-testid': 'remove-switch' }, 'Remove from Switchyard');
  remove.addEventListener('click', async () => {
    if (!(await confirmDialog({ title: 'Remove switch', message: `Remove ${S.name} from Switchyard? The switch itself is not changed.`, confirmLabel: 'Remove', danger: true }))) return;
    await run(remove, async () => { await del(dev(S.mac)); S.ctx.navigate('#/'); });
  });

  const newPw = h('input', { type: 'password', maxlength: '20', autocomplete: 'new-password', 'data-testid': 'new-switch-password' });
  const pwBtn = h('button', { class: 'btn', 'data-testid': 'change-switch-password' }, 'Change password');
  pwBtn.addEventListener('click', async () => {
    if (!newPw.value) return toast('Enter a new password.', 'error');
    await apply(pwBtn, () => post(`${dev(S.mac)}/password`, { new: newPw.value }), 'Switch password changed.');
  });

  const dangerous = (text, path, title, message, done) => {
    const b = h('button', { class: 'btn danger', 'data-testid': `do-${path}` }, text);
    b.addEventListener('click', async () => {
      const typed = await confirmDialog({ title, message, confirmLabel: text, danger: true, typeToConfirm: S.mac });
      if (!typed) return;
      await apply(b, () => post(`${dev(S.mac)}/${path}`, { confirm: typed.toLowerCase() }), done);
    });
    return b;
  };

  const file = h('input', { type: 'file', accept: '.json,application/json', 'data-testid': 'restore-file', 'aria-label': 'Backup file' });
  const withNet = h('input', { type: 'checkbox', 'data-testid': 'restore-network' });
  const steps = h('div', { 'data-testid': 'restore-steps' });
  const restoreBtn = h('button', { class: 'btn', 'data-testid': 'restore' }, 'Restore');
  restoreBtn.addEventListener('click', async () => {
    const f = file.files && file.files[0];
    if (!f) return toast('Choose a backup file first.', 'error');
    let backup;
    try { backup = JSON.parse(await f.text()); } catch (e) { return toast('This file is not valid JSON.', 'error'); }
    if (!(await confirmDialog({ title: 'Restore configuration', message: 'This overwrites VLAN, QoS, mirroring and IGMP settings on the switch with those from the file.', confirmLabel: 'Restore' }))) return;
    const res = await run(restoreBtn, () => withSwitchAuth(S.mac, S.name, () => post(`${dev(S.mac)}/restore`, { backup, include_network: withNet.checked })));
    if (!res) return;
    clear(steps).append(h('ul', {}, ...res.steps.map((s) => h('li', { class: s.ok ? 'ok-text' : 'error' }, `${s.ok ? '✓' : '✗'} ${s.step}${s.error ? `: ${s.error}` : ''}`))));
    toast(res.ok ? 'Configuration restored.' : 'Restore finished with errors.', res.ok ? 'ok' : 'error');
  });
  const backupBtn = h('button', { class: 'btn', 'data-testid': 'backup' }, 'Download backup');
  backupBtn.addEventListener('click', () => run(backupBtn, async () => {
    const data = await get(`${dev(S.mac)}/backup`);
    download(`switchyard-${data.switch.model}-${S.mac.replace(/:/g, '')}.json`, JSON.stringify(data, null, 2));
  }));

  const portIds = S.state.ports.map((p) => p.port);
  const vlanIds = [...new Set([...(S.state.vlan ? S.state.vlan.dot1q.map((v) => v.id) : []), ...(S.state.vlan ? S.state.vlan.port_vlans.map((v) => v.id) : []), ...Object.keys(d.vlan_names || {}).map(Number)])].sort((a, b) => a - b);
  const portInputs = Object.fromEntries(portIds.map((p) => [p, h('input', { type: 'text', maxlength: '30', value: (d.port_labels || {})[p] || '', 'aria-label': `Name of port ${p}`, 'data-testid': `label-port-${p}` })]));
  const vlanInputs = Object.fromEntries(vlanIds.map((v) => [v, h('input', { type: 'text', maxlength: '30', value: (d.vlan_names || {})[v] || '', 'aria-label': `Name of VLAN ${v}`, 'data-testid': `label-vlan-${v}` })]));
  const saveNames = h('button', { class: 'btn primary', 'data-testid': 'labels-save' }, 'Save names');
  saveNames.addEventListener('click', () => run(saveNames, async () => {
    await put(`${dev(S.mac)}/labels`, { ports: Object.fromEntries(portIds.map((p) => [p, portInputs[p].value])), vlans: Object.fromEntries(vlanIds.map((v) => [v, vlanInputs[v].value])) });
    toast('Names saved.'); await S.reload();
  }));
  host.append(
    section('This entry', field('Label', label), field('Notes', notes), field('Fixed address', address, 'Set this for a switch on another subnet; Switchyard then talks to it directly.'),
      h('div', { class: 'row' }, saveMeta, forget, remove)),
    section('Port and VLAN names', h('p', { class: 'muted' }, 'Names are kept by Switchyard (not on the switch) and shown in the port panel, the tables and the editors.'),
      h('div', { class: 'grid' }, ...portIds.map((p) => field(`Port ${p}`, portInputs[p])), ...vlanIds.map((v) => field(`VLAN ${v}`, vlanInputs[v]))), h('p', {}, saveNames)),
    section('Switch password', h('p', { class: 'muted' }, d.has_password ? 'A password is stored for this switch.' : 'No password is stored. You are asked when you change something.'),
      field('New password', newPw, '1 to 20 characters.'), h('p', {}, pwBtn)),
    section('Backup and restore', h('p', { class: 'muted' }, 'A backup holds VLANs, QoS, mirroring, IGMP and loop detection plus name and IP settings.'),
      h('p', {}, backupBtn), h('hr'), field('Backup file', file), h('label', { class: 'check' }, withNet, ' Also restore name and IP settings'), h('p', {}, restoreBtn), steps),
    section('Restart and reset',
      h('div', { class: 'row' },
        dangerous('Reboot', 'reboot', 'Reboot switch', 'The switch restarts and drops traffic for about half a minute.', 'The switch is restarting.'),
        dangerous('Factory reset', 'factory-reset', 'Factory reset', 'All settings are erased, including the password, which becomes "password" again.', 'The switch is back to factory settings.'))),
  );
}

export const TABS = [
  { id: 'overview', label: 'Overview', render: overview },
  { id: 'network', label: 'Network', render: network },
  { id: 'ports', label: 'Ports', render: ports },
  { id: 'vlan', label: 'VLAN', render: vlan },
  { id: 'qos', label: 'QoS', render: qos },
  { id: 'features', label: 'Mirroring & multicast', render: features },
  { id: 'ports-config', label: 'Speed & flow', render: portSettings },
  { id: 'lag', label: 'Link aggregation', render: lagTab },
  { id: 'cable', label: 'Cable test', render: cable },
  { id: 'firmware', label: 'Firmware', render: firmware },
  { id: 'maintenance', label: 'Maintenance', render: maintenance },
];
