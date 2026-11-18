import { get, post } from '../api.js';
import { h } from '../util.js';
import { run, section, toast } from '../ui.js';

export async function renderSettings(main, ctx, session) {
  const sys = await get('/system');
  const rows = [['Version', sys.version], ['Network interfaces used', sys.routes.join(', ') || 'none'],
    ['UDP port here / on switches', `${sys.client_port} / ${sys.device_port}`], ['Fixed targets', sys.targets.join(', ') || 'none (broadcast)'],
    ['Configuration file', sys.config_file], ['Managed switches', sys.devices]];
  main.append(h('div', { class: 'page-head' }, h('h1', {}, 'Settings')),
    section('System', h('dl', { class: 'kv', 'data-testid': 'system' }, ...rows.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))])),
      h('h3', {}, 'Network interfaces found'),
      sys.interfaces.length ? h('ul', {}, ...sys.interfaces.map((i) => h('li', {}, `${i.name}: ${i.ip} (${i.mac})`))) : h('p', { class: 'muted' }, 'None. Is the container using host networking?'),
      h('p', {}, h('a', { href: '/api/openapi.json', target: '_blank', rel: 'noopener' }, 'API description (OpenAPI JSON)'))));
  if (sys.auth_disabled || sys.password_from_config) {
    main.append(section('Web interface password', h('p', { class: 'muted' }, sys.auth_disabled ? 'Login is disabled in the configuration.' : 'The password is set in the configuration file or environment. Change it there.')));
    return;
  }
  const cur = h('input', { type: 'password', id: 'cur', autocomplete: 'current-password', 'data-testid': 'pw-current' });
  const next = h('input', { type: 'password', id: 'new', autocomplete: 'new-password', 'data-testid': 'pw-new' });
  const btn = h('button', { class: 'btn primary', 'data-testid': 'pw-save' }, 'Change password');
  btn.addEventListener('click', () => run(btn, async () => {
    if (next.value.length < 8) return toast('The new password needs at least 8 characters.', 'error');
    const r = await post('/password', { current: cur.value, new: next.value });
    toast(r.message);
    location.reload();
  }));
  main.append(section('Web interface password', h('label', { for: 'cur' }, 'Current password'), cur, h('label', { for: 'new' }, 'New password (8+ characters)'), next, h('p', {}, btn)));
}
