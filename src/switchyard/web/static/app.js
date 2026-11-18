// Entry point: session handling, shell and hash router.
import { api, get, onUnauthorized, post } from './api.js';
import { clear, h } from './util.js';
import { showError, toast } from './ui.js';
import { renderSwitches } from './views/switches.js';
import { renderDetail } from './views/detail.js';
import { renderLog } from './views/log.js';
import { renderSettings } from './views/settings.js';

const root = document.getElementById('app');
const state = { session: null, cleanup: null, dirty: () => false };

export const ctx = {
  navigate(hash) {
    location.hash = hash;
  },
  setDirty(fn) {
    state.dirty = fn;
  },
  onCleanup(fn) {
    state.cleanup = fn;
  },
};

const LOGO = () => h('img', { src: 'favicon.svg', alt: '', width: '28', height: '28', class: 'logo' });

export function applyTheme(choice) {
  if (choice === 'light' || choice === 'dark') document.documentElement.dataset.theme = choice;
  else delete document.documentElement.dataset.theme;
}

function themeToggle() {
  const order = ['auto', 'light', 'dark'];
  const saved = localStorage.getItem('theme') || 'auto';
  const btn = h('button', { class: 'btn small ghost', 'data-testid': 'theme', 'aria-label': 'Change theme' }, `Theme: ${saved}`);
  btn.addEventListener('click', () => {
    const next = order[(order.indexOf(localStorage.getItem('theme') || 'auto') + 1) % 3];
    localStorage.setItem('theme', next);
    applyTheme(next);
    btn.textContent = `Theme: ${next}`;
  });
  return btn;
}

function routeInfo() {
  const parts = (location.hash.replace(/^#\/?/, '') || '').split('/').filter(Boolean).map(decodeURIComponent);
  if (parts[0] === 'switch' && parts[1]) return { name: 'switch', mac: parts[1], tab: parts[2] || 'overview' };
  if (parts[0] === 'log') return { name: 'log' };
  if (parts[0] === 'settings') return { name: 'settings' };
  return { name: 'home' };
}

function footer(session) {
  return h(
    'footer',
    {},
    h('p', {}, 'Switchyard is an independent open-source project. It is not affiliated with, endorsed or supported by NETGEAR. Product names are used only to say what it works with.'),
    h('p', {}, `Switchyard ${session.version}`),
  );
}

function renderLogin(message) {
  clear(root);
  const pw = h('input', { type: 'password', id: 'pw', autocomplete: 'current-password', 'data-testid': 'login-password' });
  const err = h('p', { class: 'error', role: 'alert', 'data-testid': 'login-error' }, message || '');
  const form = h(
    'form',
    { class: 'card login', onsubmit: async (e) => {
      e.preventDefault();
      try {
        await post('/login', { password: pw.value });
        await boot();
      } catch (ex) {
        err.textContent = ex.message;
        pw.select();
      }
    } },
    h('div', { class: 'brand' }, LOGO(), 'Switchyard'),
    h('p', { class: 'muted' }, 'Sign in to manage your switches.'),
    h('label', { for: 'pw' }, 'Password'),
    pw,
    err,
    h('p', {}, h('button', { class: 'btn primary', type: 'submit', 'data-testid': 'login-submit' }, 'Sign in')),
    h('p', { class: 'muted' }, 'First start without a configured password? Look for the generated one in the server log.'),
  );
  root.append(h('main', { id: 'main' }, form));
  pw.focus();
}

async function render() {
  if (state.cleanup) state.cleanup();
  state.cleanup = null;
  state.dirty = () => false;
  const route = routeInfo();
  const session = state.session;
  clear(root);
  const nav = h(
    'nav',
    { class: 'nav', 'aria-label': 'Main' },
    ...[
      ['#/', 'Switches', 'home'],
      ['#/log', 'Activity', 'log'],
      ['#/settings', 'Settings', 'settings'],
    ].map(([href, text, key]) =>
      h('a', { href, 'aria-current': route.name === key || (key === 'home' && route.name === 'switch') ? 'page' : null, 'data-testid': `nav-${key}` }, text),
    ),
  );
  const actions = h('div', { class: 'topbar-actions' }, themeToggle());
  if (!session.auth_disabled) {
    actions.append(
      h('button', { class: 'btn small', 'data-testid': 'logout', onclick: async () => {
        await post('/logout');
        await boot();
      } }, 'Log out'),
    );
  }
  root.append(
    session.demo ? h('div', { class: 'banner', 'data-testid': 'demo-banner' }, 'Demo mode: simulated switches, no network is used.') : null,
    h('header', { class: 'topbar' }, h('a', { class: 'brand', href: '#/' }, LOGO(), 'Switchyard'), nav, actions),
  );
  const main = h('main', { id: 'main' });
  root.append(main, footer(session));
  try {
    if (route.name === 'home') await renderSwitches(main, ctx);
    else if (route.name === 'switch') await renderDetail(main, ctx, route);
    else if (route.name === 'log') await renderLog(main, ctx);
    else await renderSettings(main, ctx, session);
  } catch (err) {
    showError(err);
  }
}

async function boot() {
  const session = await get('/session').catch(() => null);
  if (!session) {
    clear(root);
    root.append(h('main', {}, h('p', { class: 'error' }, 'Cannot reach the Switchyard server.')));
    return;
  }
  state.session = session;
  if (!session.authenticated) return renderLogin();
  return render();
}

window.addEventListener('hashchange', () => {
  if (state.session && state.session.authenticated) render();
});
window.addEventListener('beforeunload', (e) => {
  if (state.dirty()) {
    e.preventDefault();
    e.returnValue = '';
  }
});
onUnauthorized(() => {
  if (state.session && state.session.authenticated) {
    state.session.authenticated = false;
    if (state.cleanup) state.cleanup();
    renderLogin('Your session ended. Please sign in again.');
  }
});

applyTheme(localStorage.getItem('theme') || 'auto');
boot();
export { api, toast };
