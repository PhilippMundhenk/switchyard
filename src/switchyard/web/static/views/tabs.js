// The sections of the switch detail page.
import { ApiError, del, dev, get, patch, post, put } from '../api.js';
import { clear, download, fmtBytes, fmtLimit, fmtRate, h, portList, validateDot1q, validateNetwork } from '../util.js';
import { drawChart } from './chart.js';
import { copyText, confirmDialog, field, modal, run, section, select, showError, toast, unsupported, withSwitchAuth } from '../ui.js';

const portName = (S, p) => (S.state.device.port_labels || {})[p] || '';
const vlanName = (S, id) => (S.state.device.vlan_names || {})[id] || '';
const portHead = (S, p) => h('th', { scope: 'row', class: 'porthead' }, String(p), portName(S, p) ? h('small', { class: 'muted pname' }, portName(S, p)) : null);

// On small screens a table with this class shows one block per row, each cell with its column name.
export function stackable(table) {
  const names = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
  table.classList.add('stack');
  for (const row of table.querySelectorAll('tbody tr')) {
    [...row.children].forEach((cell, i) => { if (names[i]) cell.dataset.label = names[i]; });
  }
  return table;
}

const isPhone = () => window.matchMedia('(max-width: 640px)').matches;

// Phone editor for per-port settings: a row of port chips and one form that sets the value of
// every selected port. The per-port controls stay the model, so Apply works unchanged.
function portChipEditor(S, ports, fields, onChange) {
  const selected = new Set([ports[0]]);
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Ports to edit', 'data-testid': 'port-chips' });
  const form = h('div', { class: 'chip-form', 'data-testid': 'chip-form' });
  const summary = h('ul', { class: 'chip-summary muted', 'data-testid': 'chip-summary' });
  const textOf = (c) => (c.type === 'checkbox' ? (c.checked ? 'on' : 'off') : c.options[c.selectedIndex].text);
  const isDefault = (f, p) => {
    const c = f.controls[p];
    if (f.neutral) return f.neutral(c);
    return c.type === 'checkbox' ? !c.checked : c.selectedIndex === 0;
  };
  const draw = () => {
    clear(chips);
    for (const p of ports) {
      const changed = fields.some((f) => !isDefault(f, p));
      chips.append(h('button', { class: `chip ${selected.has(p) ? 'on' : ''} ${changed ? 'changed' : ''}`, 'aria-pressed': String(selected.has(p)), 'aria-label': `Port ${p}${changed ? ', has settings' : ''}`, 'data-testid': `chip-${p}`,
        onclick: () => { if (selected.has(p) && selected.size > 1) selected.delete(p); else selected.add(p); draw(); } }, String(p), portName(S, p) ? h('small', {}, portName(S, p)) : null));
    }
    chips.append(h('button', { class: 'chip all', 'data-testid': 'chip-all', onclick: () => { ports.forEach((p) => selected.add(p)); draw(); } }, 'All'));
    const first = [...selected].sort((a, b) => a - b)[0];
    clear(form).append(h('p', { class: 'muted' }, `Port${selected.size > 1 ? 's' : ''} ${portList(selected)}`));
    for (const f of fields) {
      const model = f.controls[first];
      let editor;
      if (model.type === 'checkbox') {
        editor = h('input', { type: 'checkbox', checked: model.checked, disabled: model.disabled, 'data-testid': `edit-${f.key}` });
        form.append(h('label', { class: 'check' }, editor, ` ${f.label}`));
      } else {
        editor = model.cloneNode(true);
        editor.value = model.value;
        editor.disabled = model.disabled;
        editor.removeAttribute('data-testid');
        editor.setAttribute('data-testid', `edit-${f.key}`);
        editor.setAttribute('aria-label', f.label);
        form.append(field(f.label, editor));
      }
      editor.addEventListener('change', () => {
        for (const p of selected) {
          const c = f.controls[p];
          if (c.type === 'checkbox') c.checked = editor.checked; else c.value = editor.value;
          c.dispatchEvent(new Event('change'));
        }
        draw();
        if (onChange) onChange();
      });
    }
    clear(summary);
    for (const p of ports) {
      const parts = fields.filter((f) => !isDefault(f, p)).map((f) => `${f.short || f.label} ${textOf(f.controls[p])}`);
      if (parts.length) summary.append(h('li', {}, h('b', {}, `Port ${p}: `), parts.join(', ')));
    }
    if (!summary.childNodes.length) summary.append(h('li', {}, 'All ports use the defaults.'));
  };
  draw();
  return { element: h('div', { class: 'chip-editor' }, chips, form, summary), redraw: draw };
}

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
// Applies a change. The result shows as soon as the switch confirms it; the page then re-reads
// only the sections this tab shows, in the background (a full re-read takes seconds on some
// switches).
function applier(S) {
  return (button, fn, message) => run(button, async () => {
    const result = await withSwitchAuth(S.mac, S.name, fn);
    toast((result && result.message) || message || 'Saved.');
    S.refresh(S.tab && S.tab.parts);
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
      ...[['Model', info.model || '–'], ['Name', info.name || '–'], ['MAC address', info.mac], ['Serial number', info.serial || '–'],
        ['Firmware', info.firmware || '–'], ...(info.hardware ? [['Hardware', info.hardware]] : []), ['IP address', info.ip ? `${info.ip} ${info.dhcp ? '(DHCP)' : '(static)'}` : '–'],
        ['Subnet mask', info.netmask || '–'], ['Gateway', info.gateway || '–'], ['Ports', info.ports], ['Password scheme', info.password_mode]]
        .flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v === null || v === undefined || v === '' ? '–' : String(v))]))),
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
// TP-Link Easy Smart switches count packets (good and bad), not bytes
const packetsOnly = (p) => p.rx_bytes == null && p.rx_packets != null;
const fmtPackets = (n) => (n == null ? '–' : n >= 1e6 ? `${(n / 1e6).toFixed(1)} M pkts` : n >= 1e3 ? `${(n / 1e3).toFixed(1)} k pkts` : `${n} pkts`);
const fmtPps = (r) => (r == null ? '–' : `${r >= 100 ? Math.round(r) : r.toFixed(1)} pkt/s`);
const totalRx = (p) => (packetsOnly(p) ? fmtPackets(p.rx_packets) : fmtBytes(p.rx_bytes));
const totalTx = (p) => (packetsOnly(p) ? fmtPackets(p.tx_packets) : fmtBytes(p.tx_bytes));
const errorCount = (p) => (packetsOnly(p) ? p.errors : p.crc_errors);

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
        h('td', { class: 'num', title: packetsOnly(p) ? `${p.rx_packets} packets` : `${p.rx_bytes} bytes` }, totalRx(p)),
        h('td', { class: 'num' }, packetsOnly(p) ? fmtPps(rate(p.rx_packets, before && before.rx_packets)) : fmtRate(rate(p.rx_bytes, before && before.rx_bytes))),
        h('td', { class: 'num', title: packetsOnly(p) ? `${p.tx_packets} packets` : `${p.tx_bytes} bytes` }, totalTx(p)),
        h('td', { class: 'num' }, packetsOnly(p) ? fmtPps(rate(p.tx_packets, before && before.tx_packets)) : fmtRate(rate(p.tx_bytes, before && before.tx_bytes))),
        h('td', { class: 'num' }, String(errorCount(p) ?? 0))));
    }
    previous = { t, rows: new Map(rows.map((p) => [p.port, p])) };
  };
  const list = h('div', { class: 'portlist', 'data-testid': 'stats-list' });
  const drawList = (rows, t) => {
    clear(list);
    for (const p of rows) {
      const before = previous && previous.rows.get(p.port);
      const dt = previous ? (t - previous.t) / 1000 : 0;
      const rate = (a, b) => (before && dt > 0 && a >= b ? (a - b) / dt : null);
      const pk = packetsOnly(p);
      const rx = pk ? rate(p.rx_packets, before && before.rx_packets) : rate(p.rx_bytes, before && before.rx_bytes);
      const tx = pk ? rate(p.tx_packets, before && before.tx_packets) : rate(p.tx_bytes, before && before.tx_bytes);
      const fr = pk ? fmtPps : fmtRate;
      list.append(h('div', { class: `prow ${p.up ? '' : 'down'}`, 'data-testid': `stat-${p.port}` },
        h('span', { class: `badge ${speedClass(p)}` }, String(p.port)),
        h('div', { class: 'pmain' },
          h('div', {}, h('b', {}, p.up ? p.link : 'down'), portName(S, p.port) ? h('span', { class: 'muted' }, ` · ${portName(S, p.port)}`) : null,
            errorCount(p) ? h('span', { class: 'error' }, ` · ${errorCount(p)} ${pk ? 'bad packets' : 'CRC errors'}`) : null),
          h('div', { class: 'muted small' }, p.up && rx !== null ? `now ↓ ${fr(rx)}  ↑ ${fr(tx)}` : '')),
        h('div', { class: 'ptotals' }, h('div', { title: 'received' }, `↓ ${totalRx(p)}`), h('div', { title: 'sent' }, `↑ ${totalTx(p)}`))));
    }
  };
  const phone = isPhone();
  const render = (rows, t) => { if (phone) { drawList(rows, t); previous = { t, rows: new Map(rows.map((p) => [p.port, p])) }; } else draw(rows, t); };
  render(S.state.ports, Date.now());
  const clearBtn = h('button', { class: 'btn', 'data-testid': 'clear-counters' }, 'Clear counters');
  clearBtn.addEventListener('click', () => applier(S)(clearBtn, () => post(`${dev(S.mac)}/stats/reset`), 'Counters cleared.'));
  host.append(section('Port status and statistics',
    phone ? list : h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ...['Port', 'Link', 'Duplex', 'Received', 'Rx rate', 'Sent', 'Tx rate', S.state.ports.some(packetsOnly) ? 'Bad packets' : 'CRC errors'].map((t, i) => h('th', { class: i > 2 ? 'num' : '' }, t)))), body)),
    h('p', {}, clearBtn)));
  poll(S, async () => render((await get(`${dev(S.mac)}/ports`)).ports, Date.now()), 3000);

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
    const width = Math.max(320, Math.min(900, chartBox.clientWidth || 760));
    chartBox.append(drawChart(points, { width, height: width < 500 ? 200 : 220, format: hist.unit === 'packets/s' ? fmtPps : null }), h('div', { class: 'chart-legend' }, h('span', {}, h('i', { class: 'rx' }), 'Received'), h('span', {}, h('i', { class: 'tx' }), 'Sent')));
  };
  which.addEventListener('change', () => drawHistory().catch(showError));
  range.addEventListener('change', () => drawHistory().catch(showError));
  const csv = h('a', { class: 'btn small', 'data-testid': 'chart-csv', href: `/api${dev(S.mac)}/history.csv?minutes=60`, download: 'history.csv' }, 'Download CSV');
  range.addEventListener('change', () => { csv.href = `/api${dev(S.mac)}/history.csv?minutes=${range.value}`; });
  host.append(section('Traffic history', h('div', { class: 'row' }, which, range, csv), chartBox));
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
  // TP-Link has one port-based and one 802.1Q mode, without basic/advanced
  const modes = v.modes || Object.keys(MODE_LABELS);
  const short = !modes.includes('dot1q_basic');
  const label = (m) => (short ? { port_basic: 'Port-based', dot1q_advanced: '802.1Q', mtu: 'MTU VLAN' }[m] : null) || MODE_LABELS[m] || m;
  const modeSel = select(modes.map((m) => [m, label(m)]), v.mode, { 'aria-label': 'VLAN mode', 'data-testid': 'vlan-mode' });
  // MTU VLAN (TP-Link): every port talks to one uplink port only
  const uplink = select(allPorts.map((p) => [p, `Port ${p}`]), v.mtu_uplink || 1, { 'aria-label': 'Uplink port', 'data-testid': 'vlan-uplink' });
  const uplinkField = h('label', { class: 'inline', hidden: modeSel.value !== 'mtu', 'data-testid': 'vlan-uplink-field' }, 'Uplink port', uplink);
  modeSel.addEventListener('change', () => { uplinkField.hidden = modeSel.value !== 'mtu'; });
  const modeBtn = h('button', { class: 'btn', 'data-testid': 'vlan-mode-apply' }, 'Change mode');
  modeBtn.addEventListener('click', async () => {
    const body = { mode: modeSel.value };
    if (modeSel.value === 'mtu') body.uplink = Number(uplink.value);
    if (modeSel.value === v.mode && !(v.mode === 'mtu' && body.uplink !== v.mtu_uplink)) return;
    if (!(await confirmDialog({ title: 'Change VLAN mode', message: 'Changing the VLAN mode erases all current VLAN settings on the switch.', confirmLabel: 'Change mode', danger: true }))) return;
    await apply(modeBtn, () => put(`${dev(S.mac)}/vlan/mode`, body), 'VLAN mode changed.');
  });
  const hints = short
    ? [h('p', { class: 'muted' }, 'Port-based: each port belongs to exactly one group, and ports in the same group can talk to each other. 802.1Q: VLANs with tagged and untagged ports and a PVID per port. MTU VLAN: every port talks to the uplink port only.'),
      modes.includes('disabled') ? null : h('p', { class: 'muted', 'data-testid': 'vlan-default-hint' }, 'VLANs cannot be switched off on this switch. Its default is port-based with every port in group 1, which separates nothing.'),
      v.pvid_reset_on_disable ? h('p', { class: 'muted', 'data-testid': 'pvid-hint' }, 'All PVIDs are 1 by default, and the switch resets them to 1 when 802.1Q is switched off.') : null]
    : [h('p', { class: 'muted' }, 'Basic modes are simpler on the switch itself; advanced modes allow overlapping groups and tagging.')];
  host.append(section('VLAN mode', h('div', { class: 'row' }, modeSel, uplinkField, modeBtn), ...hints));
  if (v.mode === 'disabled') return host.append(section('VLANs', h('p', { class: 'muted' }, 'VLANs are disabled. Pick a mode above to start.')));
  if (v.mode === 'mtu') return host.append(section('MTU VLAN', h('p', { 'data-testid': 'mtu-info' }, `Every port talks to the uplink port ${v.mtu_uplink} only, not to each other.`)));

  const dirtyNote = h('span', { class: 'dirty', 'data-testid': 'dirty', hidden: true }, 'Unsaved changes');
  const errBox = h('div', { class: 'error', role: 'alert', 'data-testid': 'vlan-errors' });
  const matrixBox = h('div', { class: 'table-wrap', 'data-testid': 'vlan-matrix' });
  const applyBtn = h('button', { class: 'btn primary', 'data-testid': 'vlan-apply' }, 'Apply');
  const discard = h('button', { class: 'btn', 'data-testid': 'vlan-discard' }, 'Discard changes');
  
  if (v.mode.startsWith('port')) {
    const original = v.port_vlans.map((g) => ({ id: g.id, members: new Set(g.members) }));
    let model = original.map((g) => ({ id: g.id, members: new Set(g.members) }));
    const snapshot = (m) => JSON.stringify(m.map((g) => [g.id, [...g.members].sort()]));
    const draw = () => {
      const dirty = snapshot(model) !== snapshot(original);
      dirtyNote.hidden = !dirty; applyBtn.disabled = !dirty;
      S.ctx.setDirty(() => dirty);
      clear(matrixBox).append(h('table', { class: 'matrix' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Port'), ...model.map((g) => h('th', { 'data-testid': `vlan-row-${g.id}` }, `Group ${g.id}`)))),
        h('tbody', {}, ...allPorts.map((p) => h('tr', {}, portHead(S, p),
          ...model.map((g) => h('td', {}, h('button', { class: `cell ${g.members.has(p) ? 'M' : ''}`, 'aria-pressed': String(g.members.has(p)), 'aria-label': `Group ${g.id}, port ${p}`, 'data-testid': `cell-${g.id}-${p}`,
            onclick: () => toggle(g, p) }, g.members.has(p) ? '✓' : '–'))))))));
    };
    // TP-Link: a port is in exactly one group, and group 1 takes every port of no other group
    const toggle = (g, p) => {
      if (!v.exclusive_groups) { if (g.members.has(p)) g.members.delete(p); else g.members.add(p); return draw(); }
      if (g.members.has(p)) {
        if (g.id === 1) return toast('Put the port into another group to take it out of group 1.', 'error');
        g.members.delete(p);
        model.find((x) => x.id === 1)?.members.add(p);
      } else {
        for (const other of model) other.members.delete(p);
        g.members.add(p);
      }
      return draw();
    };
    const limit = v.max_vlans || 8;
    const addBtn = h('button', { class: 'btn', 'data-testid': 'vlan-add-group' }, 'Add group');
    addBtn.addEventListener('click', () => {
      const used = new Set(model.map((g) => g.id));
      const id = Array.from({ length: limit }, (_, i) => i + 1).find((x) => !used.has(x));
      if (!id) return toast(`This switch has at most ${limit} groups.`, 'error');
      model.push({ id, members: new Set() });
      model.sort((a, b) => a.id - b.id);
      return draw();
    });
    draw();
    applyBtn.addEventListener('click', () => apply(applyBtn, () => put(`${dev(S.mac)}/vlan/port-based`, { vlans: model.map((g) => ({ id: g.id, members: [...g.members].sort((a, b) => a - b) })) }), 'VLANs saved.'));
    discard.addEventListener('click', () => { model = original.map((g) => ({ id: g.id, members: new Set(g.members) })); draw(); });
    const groupHint = v.exclusive_groups
      ? h('p', { class: 'muted' }, 'Each port is in exactly one group. Ports leave group 1 by joining another group; a group without ports is removed when applied. PVIDs exist in 802.1Q mode only.')
      : null;
    return host.append(section('Port-based VLAN groups', groupHint, matrixBox, h('div', { class: 'row' }, addBtn, applyBtn, discard, dirtyNote)));
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
      return h('td', { class: 'pvid' }, sel);
    };
    const vlanHead = (vl) => h('th', { 'data-testid': `vlan-row-${vl.id}` },
      h('div', {}, `VLAN ${vl.id}`), vlanName(S, vl.id) ? h('small', { class: 'muted vname' }, vlanName(S, vl.id)) : null,
      vl.id === 1 ? null : h('button', { class: 'btn small ghost xs', 'aria-label': `Remove VLAN ${vl.id}`, 'data-testid': `remove-vlan-${vl.id}`, title: `Remove VLAN ${vl.id}`,
        onclick: () => { vlans = vlans.filter((x) => x !== vl); for (const p of allPorts) if (pvid[p] === vl.id) pvid[p] = 1; draw(); } }, '✕'));
    clear(matrixBox).append(h('table', { class: 'matrix' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Port'), ...vlans.map(vlanHead), h('th', {}, 'PVID'))),
      h('tbody', {}, ...allPorts.map((p) => h('tr', {}, portHead(S, p), ...vlans.map((vl) => h('td', {}, cellOf(vl, p))), pvidCell(p))))));
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
  const modeLabels = { dot1p: '802.1p based', port: 'Port based', dscp: 'DSCP/802.1p based' };
  const mode = select((q.modes || ['dot1p', 'port']).map((m) => [m, modeLabels[m] || m]), q.mode, { 'data-testid': 'qos-mode' });
  const prio = {}; const ingress = {}; const egress = {}; const storm = {};
  // TP-Link keeps storm control per port: rate and frame types (#111)
  const perPort = Boolean(q.storm_ports);
  const typeNames = { unknown_unicast: 'Unknown unicast', multicast: 'Multicast', broadcast: 'Broadcast' };
  const stormTypes = {};
  const table = h('table', { class: 'controls' }, h('thead', {}, h('tr', {}, ...['Port', 'Priority', 'Ingress limit', 'Egress limit', ...(perPort ? [] : ['Storm control rate'])].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...allPorts.map((p) => {
      prio[p] = select(['high', 'medium', 'normal', 'low'].map((x) => [x, x[0].toUpperCase() + x.slice(1)]), q.priorities[p] || 'normal', { 'aria-label': `Priority of port ${p}`, 'data-testid': `prio-${p}` });
      ingress[p] = select(rates, q.ingress[p] ?? 0, { 'aria-label': `Ingress limit of port ${p}`, 'data-testid': `ingress-${p}` });
      egress[p] = select(rates, q.egress[p] ?? 0, { 'aria-label': `Egress limit of port ${p}`, 'data-testid': `egress-${p}` });
      storm[p] = select(rates, perPort ? (q.storm_ports[p]?.rate ?? 0) : (q.storm_rates[p] ?? 0), { 'aria-label': `Storm control rate of port ${p}`, 'data-testid': `storm-${p}` });
      if (perPort) stormTypes[p] = Object.fromEntries(Object.keys(typeNames).map((k) => [k, h('input', { type: 'checkbox', checked: q.storm_ports[p] && q.storm_ports[p].rate ? Boolean(q.storm_ports[p][k]) : true, 'aria-label': `${typeNames[k]} on port ${p}`, 'data-testid': `storm-${k}-${p}` })]));
      return h('tr', {}, h('td', {}, String(p)), h('td', {}, prio[p]), h('td', {}, ingress[p]), h('td', {}, egress[p]), perPort ? null : h('td', {}, storm[p]));
    })));
  const stormTable = perPort ? stackable(h('table', { class: 'controls', 'data-testid': 'storm-table' },
    h('thead', {}, h('tr', {}, ...['Port', 'Rate (no limit = off)', ...Object.values(typeNames)].map((t) => h('th', {}, t)))),
    h('tbody', {}, ...allPorts.map((p) => h('tr', {}, h('td', {}, String(p)), h('td', {}, storm[p]), ...Object.keys(typeNames).map((k) => h('td', {}, stormTypes[p][k]))))))) : null;
  const stormOn = h('input', { type: 'checkbox', checked: Boolean(q.storm_control), disabled: q.storm_control === null, 'data-testid': 'storm-enabled' });
  let editor = null;
  const sync = () => { for (const p of allPorts) { prio[p].disabled = mode.value !== 'port'; if (!perPort) storm[p].disabled = !stormOn.checked; } if (editor) editor.redraw(); };
  if (isPhone()) {
    editor = portChipEditor(S, allPorts, [
      { key: 'prio', label: 'Priority', controls: prio, neutral: (c) => c.value === 'normal' },
      { key: 'ingress', label: 'Ingress limit', short: 'in', controls: ingress },
      { key: 'egress', label: 'Egress limit', short: 'out', controls: egress },
      ...(perPort ? [] : [{ key: 'storm', label: 'Storm control rate', short: 'storm', controls: storm }]),
    ]);
  }
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
    if (perPort) {
      body.storm_ports = Object.fromEntries(allPorts.map((p) => [p, { rate: Number(storm[p].value), ...Object.fromEntries(Object.entries(stormTypes[p]).map(([k, box]) => [k, box.checked])) }]));
      const bad = allPorts.find((p) => body.storm_ports[p].rate && !Object.keys(typeNames).some((k) => body.storm_ports[p][k]));
      if (bad) return toast(`Port ${bad}: choose at least one frame type for storm control.`, 'error');
    } else if (q.storm_control !== null) {
      body.storm_control = stormOn.checked;
      if (stormOn.checked) body.storm_rates = Object.fromEntries(allPorts.map((p) => [p, Number(storm[p].value)]));
    }
    await apply(save, () => put(`${dev(S.mac)}/qos`, body), 'QoS settings saved.');
  });
  host.append(section('Quality of service', field('QoS mode', mode, 'Port priorities can be set in port-based mode only.'),
    perPort ? null : h('label', { class: 'check' }, stormOn, ' Broadcast storm control'),
    editor ? editor.element : h('div', { class: 'table-wrap' }, table),
    perPort ? h('h4', {}, 'Storm control') : null,
    perPort ? h('p', { class: 'muted' }, 'Per port: a rate turns storm control on for the chosen frame types; "no limit" turns it off. A port has either an ingress limit or storm control.') : null,
    perPort ? h('div', { class: 'table-wrap' }, stormTable) : null,
    h('p', {}, save)));
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
    // switches that report directions (TP-Link) mirror received and sent traffic separately
    const directional = Array.isArray(m.ingress);
    const dirOf = (p) => {
      const rx = (m.ingress || []).includes(p) && m.destination;
      const tx = (m.egress || []).includes(p) && m.destination;
      return rx && tx ? 'both' : rx ? 'rx' : tx ? 'tx' : 'none';
    };
    const boxes = allPorts.map((p) => h('input', { type: 'checkbox', checked: m.sources.includes(p), 'data-testid': `mirror-src-${p}` }));
    const dirs = allPorts.map((p) => select([['none', 'not mirrored'], ['both', 'received and sent'], ['rx', 'received only'], ['tx', 'sent only']], dirOf(p), { 'aria-label': `Port ${p}`, 'data-testid': `mirror-dir-${p}` }));
    const save = h('button', { class: 'btn primary', 'data-testid': 'mirror-save' }, 'Apply');
    save.addEventListener('click', () => {
      const d = Number(dest.value);
      const body = { destination: d, sources: [] };
      if (directional) {
        body.ingress = d ? allPorts.filter((p, i) => ['both', 'rx'].includes(dirs[i].value)) : [];
        body.egress = d ? allPorts.filter((p, i) => ['both', 'tx'].includes(dirs[i].value)) : [];
        body.sources = [...new Set([...body.ingress, ...body.egress])];
      } else body.sources = d ? allPorts.filter((p, i) => boxes[i].checked) : [];
      if (d && body.sources.includes(d)) return toast('The destination port cannot also be a source port.', 'error');
      if (d && !body.sources.length) return toast('Select at least one source port.', 'error');
      return apply(save, () => put(`${dev(S.mac)}/mirror`, body), 'Mirroring saved.');
    });
    // one source port only (TP-Link): pick the port and its direction
    const single = directional && m.max_sources === 1;
    const current = allPorts.find((p) => dirOf(p) !== 'none') || 0;
    const srcSel = select([[0, 'None'], ...allPorts.map((p) => [p, `Port ${p}`])], current, { 'data-testid': 'mirror-source' });
    const dirSel = select([['both', 'received and sent'], ['rx', 'received only'], ['tx', 'sent only']], current ? dirOf(current) : 'both', { 'data-testid': 'mirror-direction' });
    if (single) {
      // the per-port selects drive the request below: keep them in step with the two choices
      const syncSingle = () => allPorts.forEach((p, i) => { dirs[i].value = p === Number(srcSel.value) ? dirSel.value : 'none'; });
      srcSel.addEventListener('change', syncSingle);
      dirSel.addEventListener('change', syncSingle);
      syncSingle();
    }
    const choices = single
      ? h('div', { class: 'grid' }, field('Source port', srcSel, 'This switch mirrors one port.'), field('Traffic', dirSel))
      : directional
      ? h('div', { class: 'grid' }, ...allPorts.map((p, i) => field(`Port ${p}`, dirs[i])))
      : h('div', { class: 'checks' }, ...allPorts.map((p, i) => h('label', {}, boxes[i], `Port ${p}`)));
    host.append(section('Port mirroring', field('Destination port', dest, 'Copies of the traffic of the source ports are sent here.'), choices, h('p', {}, save)));
  }

  const g = S.state.igmp;
  if (!g) host.append(section('IGMP snooping', unsupported('IGMP snooping')));
  else {
    const on = h('input', { type: 'checkbox', checked: g.enabled, 'data-testid': 'igmp-enabled' });
    const vid = h('input', { type: 'number', min: '1', max: '4093', value: g.vlan || 1, 'data-testid': 'igmp-vlan' });
    const block = h('input', { type: 'checkbox', checked: Boolean(g.block_unknown), disabled: g.block_unknown === null, 'data-testid': 'igmp-block' });
    const validate = h('input', { type: 'checkbox', checked: Boolean(g.validate_v3), disabled: g.validate_v3 === null, 'data-testid': 'igmp-validate' });
    const suppress = h('input', { type: 'checkbox', checked: Boolean(g.report_suppression), 'data-testid': 'igmp-suppress' });
    const hasVlan = g.vlan !== null && g.vlan !== undefined; // TP-Link: no VLAN setting
    const hasSuppress = g.report_suppression !== undefined;
    const save = h('button', { class: 'btn primary', 'data-testid': 'igmp-save' }, 'Apply');
    save.addEventListener('click', () => {
      const id = Number(vid.value);
      if (hasVlan && on.checked && (!Number.isInteger(id) || id < 1 || id > 4093)) return toast('VLAN ID must be 1 to 4093.', 'error');
      const payload = { enabled: on.checked, vlan: hasVlan ? id || 1 : 1, block_unknown: g.block_unknown === null ? null : block.checked, validate_v3: g.validate_v3 === null ? null : validate.checked };
      if (hasSuppress) payload.report_suppression = suppress.checked;
      return apply(save, () => put(`${dev(S.mac)}/igmp`, payload), 'IGMP snooping saved.');
    });
    host.append(section('IGMP snooping', h('label', { class: 'check' }, on, ' Enable IGMP snooping'), hasVlan ? field('VLAN ID', vid) : null,
      g.block_unknown === null ? null : h('label', { class: 'check' }, block, ' Block unknown multicast addresses'),
      g.validate_v3 === null ? null : h('label', { class: 'check' }, validate, ' Validate IGMPv3 IP header'),
      hasSuppress ? h('label', { class: 'check' }, suppress, ' Report message suppression') : null, h('p', {}, save)));
  }

  const loop = S.state.loop_detection;
  if (loop === null) host.append(section('Loop detection', unsupported('Loop detection')));
  else {
    const on = h('input', { type: 'checkbox', checked: loop, 'data-testid': 'loop-enabled' });
    const save = h('button', { class: 'btn primary', 'data-testid': 'loop-save' }, 'Apply');
    save.addEventListener('click', () => apply(save, () => put(`${dev(S.mac)}/loop-detection`, { enabled: on.checked }), 'Loop detection saved.'));
    host.append(section('Loop detection', h('label', { class: 'check' }, on, ' Detect network loops and block looping ports'), h('p', {}, save)));
  }

  // TP-Link: the port LEDs can be switched off (#113)
  if (typeof S.state.led === 'boolean') {
    const led = h('input', { type: 'checkbox', checked: S.state.led, 'data-testid': 'led-enabled' });
    const save = h('button', { class: 'btn primary', 'data-testid': 'led-save' }, 'Apply');
    save.addEventListener('click', () => apply(save, () => put(`${dev(S.mac)}/led`, { enabled: led.checked }), 'Port LEDs saved.'));
    host.append(section('Port LEDs', h('label', { class: 'check' }, led, ' LEDs on'), h('p', {}, save)));
  }
}

// ------------------------------------------------------------------- cable
// false when the switch's protocol cannot do it (TP-Link: firmware upgrade, cable test)
const can = (S, what) => !S.state.capabilities || S.state.capabilities[what] !== false;

async function cable(host, S) {
  if (!can(S, 'cable_test')) return host.append(section('Cable test', unsupported('The cable test')));
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
    if (done) results.append(h('div', { class: 'table-wrap' }, stackable(h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Port'), h('th', {}, 'Result'), h('th', { class: 'num' }, 'Fault distance (m)'))),
      h('tbody', {}, ...done.results.map((r) => h('tr', { 'data-testid': `cable-${r.port}` }, h('td', {}, String(r.port)), h('td', {}, labels[r.status] || r.status), h('td', { class: 'num' }, r.status === 'ok' ? '–' : String(r.distance_m)))))))));
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
  const editor = isPhone() ? portChipEditor(S, Object.keys(admin).map(Number).sort((a, b) => a - b), [
    { key: 'speed', label: 'Speed setting', short: 'speed', controls: speedSel },
    { key: 'flow', label: 'Flow control', short: 'flow control', controls: flow },
  ]) : null;
  host.append(section('Port speed and flow control',
    h('p', { class: 'muted' }, 'Auto negotiates. Fixed speeds need the same setting on the device at the other end. Members of a link aggregation group must share one setting.'),
    editor ? editor.element : h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ...['Port', 'Link now', 'Speed setting', 'Flow control'].map((t) => h('th', {}, t)))), h('tbody', {}, rows))),
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
      h('thead', {}, h('tr', {}, h('th', {}, 'Port'), ...model.map((g) => h('th', { 'data-testid': `lag-row-${g.id}` },
        h('div', {}, `LAG ${g.id}`),
        h('label', { class: 'check mini' }, h('input', { type: 'checkbox', checked: g.enabled, 'aria-label': `LAG ${g.id} active`, 'data-testid': `lag-on-${g.id}`, onchange: (e) => { g.enabled = e.target.checked; draw(); } }), 'on'))))),
      h('tbody', {}, ...allPorts.map((p) => h('tr', {}, portHead(S, p),
        ...model.map((g) => h('td', {}, h('button', { class: `cell ${g.members.has(p) ? 'M' : ''}`, disabled: !g.enabled, 'aria-pressed': String(g.members.has(p)), 'aria-label': `LAG ${g.id}, port ${p}`, 'data-testid': `lag-cell-${g.id}-${p}`,
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
  if (!can(S, 'firmware_upgrade')) {
    return host.append(section('Firmware', h('p', {}, `Installed: ${S.state.info.firmware || '?'}`),
      unsupported('A firmware upgrade through Switchyard'), h('p', { class: 'muted' }, "Use the switch's own web interface or the vendor's utility to upgrade it.")));
  }
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
      const r = await fetch(`/api${dev(S.mac)}/firmware/check`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream' }, body: buf });
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
      const r = await fetch(`/api${dev(S.mac)}/firmware?${q}`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream' }, body: checked.buf });
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
// Label, notes, fixed address, forget password, remove. Also shown when the switch does not
// answer: setting its address is often the fix.
export function entrySection(d, S, after) {
  const label = h('input', { type: 'text', value: d.label, maxlength: '60', 'data-testid': 'meta-label' });
  const notes = h('textarea', { rows: '3', maxlength: '2000', 'data-testid': 'meta-notes' }); notes.value = d.notes || '';
  const address = h('input', { type: 'text', value: d.address || '', placeholder: 'empty = find by broadcast', 'data-testid': 'meta-address' });
  const saveMeta = h('button', { class: 'btn primary', 'data-testid': 'meta-save' }, 'Save');
  saveMeta.addEventListener('click', () => run(saveMeta, async () => {
    await patch(dev(S.mac), { label: label.value, notes: notes.value, set_address: true, address: address.value.trim() || null });
    toast('Saved.'); await after('meta');
  }));
  const forget = h('button', { class: 'btn', 'data-testid': 'forget-password', disabled: !d.remembered }, 'Forget stored password');
  forget.addEventListener('click', () => run(forget, async () => { await patch(dev(S.mac), { forget_password: true }); toast('Password forgotten.'); await after('password'); }));
  const remove = h('button', { class: 'btn danger', 'data-testid': 'remove-switch' }, 'Remove from Switchyard');
  remove.addEventListener('click', async () => {
    if (!(await confirmDialog({ title: 'Remove switch', message: `Remove ${S.name} from Switchyard? The switch itself is not changed.`, confirmLabel: 'Remove', danger: true }))) return;
    await run(remove, async () => { await del(dev(S.mac)); S.ctx.navigate('#/'); });
  });
  const hint = d.unicast === false
    ? 'This switch answers only in its own network segment: a fixed address does not help.'
    : 'Set this when the switch is on another subnet, or Switchyard runs in a bridged container; Switchyard then talks to it directly.';
  return section('This entry', field('Label', label), field('Notes', notes), field('Fixed address', address, hint),
    h('div', { class: 'row' }, saveMeta, forget, remove));
}

async function maintenance(host, S) {
  const apply = applier(S);
  const d = S.state.device;
  const entry = entrySection(d, S, async (what) => {
    await S.reload();
    if (what === 'meta') S.retitle();
    else await S.show();
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
  const compareBtn = h('button', { class: 'btn', 'data-testid': 'compare-file' }, 'Compare with the switch');
  compareBtn.addEventListener('click', async () => {
    const f = file.files && file.files[0];
    if (!f) return toast('Choose a backup file first.', 'error');
    let backup;
    try { backup = JSON.parse(await f.text()); } catch (e) { return toast('This file is not valid JSON.', 'error'); }
    const res = await run(compareBtn, () => post(`${dev(S.mac)}/diff`, { backup }));
    if (res) showDiff(res);
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
  // stored backups and comparison
  const showDiff = (res) => modal({
    title: res.same ? 'No differences' : `${res.changes.length} difference${res.changes.length === 1 ? '' : 's'}`,
    body: res.same ? h('p', {}, 'The switch matches this backup.') : h('div', { 'data-testid': 'diff-list' }, h('p', { class: 'muted' }, 'A restore would change:'), h('ul', {}, ...res.changes.map((c) => h('li', {}, h('b', {}, `${c.area}: `), c.text)))),
    actions: (close) => [h('button', { class: 'btn primary', onclick: close }, 'Close')],
  });
  const backupRows = h('tbody', { 'data-testid': 'backup-rows' });
  const loadBackups = async () => {
    const { backups, interval_hours: every } = await get(`${dev(S.mac)}/backups`);
    clear(backupRows);
    if (!backups.length) backupRows.append(h('tr', {}, h('td', { colspan: '3', class: 'muted', 'data-testid': 'backups-empty' }, every ? `No stored backups yet. One is made every ${every} h when the configuration changed.` : 'No stored backups. Automatic backups are off.')));
    for (const b of backups) {
      const name = b.name;
      backupRows.append(h('tr', { 'data-testid': 'backup-row', 'data-name': name },
        h('td', {}, new Date(b.created * 1000).toLocaleString()), h('td', { class: 'num' }, fmtBytes(b.size)),
        h('td', {}, h('div', { class: 'row' },
          h('button', { class: 'btn small', 'data-testid': 'backup-compare', onclick: (e) => run(e.target, async () => showDiff(await post(`${dev(S.mac)}/diff`, { stored: name }))) }, 'Compare'),
          h('button', { class: 'btn small', 'data-testid': 'backup-restore', onclick: async (e) => {
            if (!(await confirmDialog({ title: 'Restore backup', message: 'This overwrites VLAN, QoS, mirroring, IGMP, LAG and port settings on the switch with those of this backup.', confirmLabel: 'Restore' }))) return;
            const res = await run(e.target, () => withSwitchAuth(S.mac, S.name, () => post(`${dev(S.mac)}/restore`, { stored: name })));
            if (res) toast(res.ok ? 'Configuration restored.' : 'Restore finished with errors.', res.ok ? 'ok' : 'error');
          } }, 'Restore'),
          h('a', { class: 'btn small', href: `/api${dev(S.mac)}/backups/${name}`, download: name }, 'Download'),
          h('button', { class: 'btn small ghost', 'data-testid': 'backup-delete', onclick: (e) => run(e.target, async () => { await del(`${dev(S.mac)}/backups/${name}`); await loadBackups(); }) }, 'Delete')))));
    }
    if (backupRows.parentNode) stackable(backupRows.parentNode);
  };
  const backupNow = h('button', { class: 'btn', 'data-testid': 'backup-now' }, 'Back up now');
  backupNow.addEventListener('click', () => run(backupNow, async () => { await post(`${dev(S.mac)}/backups`); toast('Backup stored.'); await loadBackups(); }));
  loadBackups().catch(() => {});

  const scanBody = h('tbody', { 'data-testid': 'diag-body' });
  const scanOut = h('div', { 'data-testid': 'diag-out' });
  const scanBtn = h('button', { class: 'btn', 'data-testid': 'diag-run' }, 'Scan settings');
  const copyBtn = h('button', { class: 'btn', 'data-testid': 'diag-copy', hidden: true }, 'Copy for bug report');
  let scanText = '';
  scanBtn.addEventListener('click', () => run(scanBtn, async () => {
    clear(scanOut).append(h('p', { class: 'muted' }, 'Reading… silent firmware needs about a second per unknown setting.'));
    const res = await get(`${dev(S.mac)}/diagnostics`);
    const rows = res.tags.map((t) => [`0x${t.tag.toString(16).padStart(4, '0')}`, t.name || '(unknown)', t.answered ? (t.values.some((v) => v) ? 'answers' : 'empty (not supported)') : t.status === null ? 'silent' : `status ${t.status}`, t.values.join(' | ') || '–']);
    scanText = `${res.info.model} fw ${res.info.firmware} (${res.info.password_mode})\n` + rows.filter((r) => r[2] !== 'silent').map((r) => r.join('  ')).join('\n');
    clear(scanOut).append(h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ...['Tag', 'Setting', 'Result', 'Raw value(s)'].map((x) => h('th', {}, x)))),
      h('tbody', { 'data-testid': 'diag-body' }, ...rows.map((r) => h('tr', { 'data-testid': 'diag-row', 'data-answers': String(r[2] === 'answers') }, h('td', { class: 'mono' }, r[0]), h('td', {}, r[1]), h('td', {}, r[2]), h('td', { class: 'mono' }, r[3])))))));
    copyBtn.hidden = false;
  }));
  copyBtn.addEventListener('click', () => copyText(scanText, 'switchyard-diagnostics.txt'));

  host.append(
    entry,
    section('Automatic backups', h('p', { class: 'muted' }, 'Stored on the server in the data folder. Backups contain the configuration, never passwords.'),
      h('div', { class: 'table-wrap' }, h('table', { class: 'backups' }, h('thead', {}, h('tr', {}, h('th', {}, 'Created'), h('th', { class: 'num' }, 'Size'), h('th', {}, ''))), backupRows)), h('p', {}, backupNow)),
    section('Diagnostics', h('p', { class: 'muted' }, 'Reads each known setting once and shows which ones this switch answers, with the raw bytes. Nothing is changed. Paste the result into a hardware report to help support more models.'),
      h('div', { class: 'row' }, scanBtn, copyBtn), scanOut),
    section('Port and VLAN names', h('p', { class: 'muted' }, 'Names are kept by Switchyard (not on the switch) and shown in the port panel, the tables and the editors.'),
      h('div', { class: 'grid' }, ...portIds.map((p) => field(`Port ${p}`, portInputs[p])), ...vlanIds.map((v) => field(`VLAN ${v}`, vlanInputs[v]))), h('p', {}, saveNames)),
    section('Switch password', h('p', { class: 'muted' }, d.has_password ? 'A password is stored for this switch.' : 'No password is stored. You are asked when you change something.'),
      field('New password', newPw, '1 to 20 characters.'), h('p', {}, pwBtn)),
    section('Backup and restore', h('p', { class: 'muted' }, 'A backup holds VLANs, QoS, mirroring, IGMP and loop detection plus name and IP settings.'),
      h('p', {}, backupBtn), h('hr'), field('Backup file', file), h('label', { class: 'check' }, withNet, ' Also restore name and IP settings'), h('div', { class: 'row' }, compareBtn, restoreBtn), steps),
    section('Restart and reset',
      h('div', { class: 'row' },
        dangerous('Reboot', 'reboot', 'Reboot switch', 'The switch restarts and drops traffic for about half a minute.', 'The switch is restarting.'),
        dangerous('Factory reset', 'factory-reset', 'Factory reset', 'All settings are erased, including the password, which becomes "password" again.', 'The switch is back to factory settings.'))),
  );
}

export const TABS = [
  { id: 'overview', label: 'Overview', render: overview, parts: null },
  { id: 'network', label: 'Network', render: network, parts: ['info'] },
  { id: 'ports', label: 'Ports', render: ports, parts: ['ports'] },
  { id: 'vlan', label: 'VLAN', render: vlan, parts: ['vlan'] },
  { id: 'qos', label: 'QoS', render: qos, parts: ['qos'] },
  { id: 'features', label: 'Mirroring & multicast', render: features, parts: ['features'] },
  { id: 'ports-config', label: 'Speed & flow', render: portSettings, parts: ['port_admin', 'ports'] },
  { id: 'lag', label: 'Link aggregation', render: lagTab, parts: ['lag', 'port_admin'] },
  { id: 'cable', label: 'Cable test', render: cable, parts: null },
  { id: 'firmware', label: 'Firmware', render: firmware, parts: null },
  { id: 'maintenance', label: 'Maintenance', render: maintenance, parts: null },
];
