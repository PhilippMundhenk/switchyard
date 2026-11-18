// Small helpers: DOM builder, formatting, validation. No dependencies.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'value') el.value = value;
    else if (key === 'checked' || key === 'disabled' || key === 'selected') el[key] = Boolean(value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let value = Number(n);
  let i = 0;
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000;
    i += 1;
  }
  return `${i === 0 ? value : value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${units[i]}`;
}

export function fmtRate(bytesPerSecond) {
  if (bytesPerSecond === null || bytesPerSecond === undefined) return '–';
  const bits = bytesPerSecond * 8;
  const units = ['bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s'];
  let value = bits;
  let i = 0;
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000;
    i += 1;
  }
  return `${i === 0 ? Math.round(value) : value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtLimit(kbit) {
  if (!kbit) return 'No limit';
  return kbit >= 1024 ? `${kbit / 1024} Mbit/s` : `${kbit} kbit/s`;
}

export function fmtAgo(seconds, now = Date.now() / 1000) {
  if (!seconds) return 'never';
  const d = Math.max(0, Math.round(now - seconds));
  if (d < 10) return 'just now';
  if (d < 60) return `${d} s ago`;
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

export function isIPv4(text) {
  const parts = String(text).trim().split('.');
  return parts.length === 4 && parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255);
}

export function ipToInt(text) {
  return String(text).split('.').reduce((acc, p) => acc * 256 + Number(p), 0);
}

export function isNetmask(text) {
  if (!isIPv4(text)) return false;
  const bits = ipToInt(text).toString(2).padStart(32, '0');
  return /^1*0*$/.test(bits) && bits !== '0'.repeat(32);
}

export function sameSubnet(a, b, mask) {
  const m = ipToInt(mask);
  return ((ipToInt(a) & m) >>> 0) === ((ipToInt(b) & m) >>> 0);
}

export function validateNetwork({ dhcp, ip, netmask, gateway }) {
  if (dhcp) return [];
  const errors = [];
  if (!isIPv4(ip)) errors.push('Enter a valid IP address.');
  if (!isNetmask(netmask)) errors.push('Enter a valid subnet mask such as 255.255.255.0.');
  if (gateway && !isIPv4(gateway)) errors.push('Enter a valid gateway address or leave it empty.');
  if (!errors.length && gateway && gateway !== '0.0.0.0' && !sameSubnet(ip, gateway, netmask)) {
    errors.push('The gateway must be in the same subnet as the switch.');
  }
  return errors;
}

export function validateDot1q(vlans, pvid, ports, maxVlans) {
  const errors = [];
  if (maxVlans && vlans.length > maxVlans) errors.push(`This switch holds at most ${maxVlans} VLANs.`);
  const byId = new Map(vlans.map((v) => [v.id, v]));
  if (!byId.has(1)) errors.push('VLAN 1 must stay in the list.');
  for (const v of vlans) {
    if (!Number.isInteger(v.id) || v.id < 1 || v.id > 4093) errors.push(`VLAN ID ${v.id} must be 1 to 4093.`);
    for (const p of v.tagged) if (!v.members.has(p)) errors.push(`VLAN ${v.id}: tagged port ${p} is not a member.`);
  }
  for (let p = 1; p <= ports; p += 1) {
    const vid = pvid[p];
    if (!byId.has(vid)) errors.push(`Port ${p}: PVID ${vid} is not an existing VLAN.`);
    else if (!byId.get(vid).members.has(p)) errors.push(`Port ${p} must be a member of VLAN ${vid} to use it as PVID.`);
  }
  return errors;
}

export function portList(ports) {
  return [...ports].sort((a, b) => a - b).join(', ') || '–';
}

export function download(filename, text, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
