// Shared UI pieces: toasts, modal dialogs, the switch password prompt.
import { api, ApiError, dev } from './api.js';
import { clear, download, h } from './util.js';

export function toast(message, kind = 'ok') {
  const root = document.getElementById('toasts');
  const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, message);
  root.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 8000 : 3500);
  return el;
}

export function showError(err) {
  if (err instanceof ApiError && err.code === 'login_required') return;
  toast(err && err.message ? err.message : String(err), 'error');
}

export function modal({ title, body, actions, onClose }) {
  const root = document.getElementById('modal-root');
  const previous = document.activeElement;
  const close = () => {
    overlay.remove();
    if (previous && previous.focus) previous.focus();
    if (onClose) onClose();
  };
  const box = h(
    'div',
    { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title, 'data-testid': 'modal' },
    h('h2', {}, title),
    body,
    h('div', { class: 'modal-actions' }, actions(close)),
  );
  const overlay = h('div', { class: 'overlay' }, box);
  overlay.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });
  clear(root).append(overlay);
  const first = box.querySelector('input, select, button');
  if (first) first.focus();
  return close;
}

export function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false, typeToConfirm = null }) {
  return new Promise((resolve) => {
    let answered = false;
    const input = typeToConfirm
      ? h('input', { type: 'text', 'aria-label': `Type ${typeToConfirm} to confirm`, autocomplete: 'off', 'data-testid': 'confirm-input' })
      : null;
    const done = (value, close) => {
      answered = true;
      close();
      resolve(value);
    };
    const body = h(
      'div',
      {},
      h('p', {}, message),
      typeToConfirm ? h('p', { class: 'muted' }, 'Type ', h('code', {}, typeToConfirm), ' to confirm.') : null,
      input,
    );
    modal({
      title,
      body,
      onClose: () => {
        if (!answered) resolve(null);
      },
      actions: (close) => {
        const ok = h(
          'button',
          {
            class: danger ? 'btn danger' : 'btn primary',
            'data-testid': 'confirm-ok',
            onclick: () => {
              if (input && input.value.trim().toLowerCase() !== typeToConfirm.toLowerCase()) {
                input.setAttribute('aria-invalid', 'true');
                input.focus();
                return;
              }
              done(input ? input.value.trim() : true, close);
            },
          },
          confirmLabel,
        );
        return [h('button', { class: 'btn', onclick: () => done(null, close) }, 'Cancel'), ok];
      },
    });
  });
}

// Asks for the password of one switch, logs in, resolves true when it worked.
export function switchLoginDialog(mac, name, reason) {
  return new Promise((resolve) => {
    let answered = false;
    const pw = h('input', { type: 'password', id: 'sw-pass', autocomplete: 'off', 'data-testid': 'switch-password' });
    const remember = h('input', { type: 'checkbox', id: 'sw-remember', checked: true, 'data-testid': 'switch-remember' });
    const message = h('p', { class: 'error', 'data-testid': 'switch-login-error', hidden: true });
    const body = h(
      'form',
      { onsubmit: (e) => e.preventDefault() },
      h('p', {}, reason || `Changing settings on ${name} needs its password.`),
      h('label', { for: 'sw-pass' }, 'Switch password'),
      pw,
      h('p', { class: 'muted' }, 'A factory-fresh switch uses ', h('code', {}, 'password'), '.'),
      h('label', { class: 'check' }, remember, ' Remember it on this server'),
      message,
    );
    modal({
      title: 'Switch password',
      body,
      onClose: () => {
        if (!answered) resolve(false);
      },
      actions: (close) => {
        const ok = h(
          'button',
          {
            class: 'btn primary',
            type: 'submit',
            'data-testid': 'switch-login-ok',
            onclick: async () => {
              ok.disabled = true;
              try {
                await api('POST', `${dev(mac)}/login`, { password: pw.value, remember: remember.checked });
                answered = true;
                close();
                resolve(true);
              } catch (err) {
                message.hidden = false;
                message.textContent = err.message;
                ok.disabled = false;
                pw.select();
              }
            },
          },
          'Log in to switch',
        );
        body.addEventListener('submit', () => ok.click());
        return [
          h('button', { class: 'btn', onclick: () => { answered = true; close(); resolve(false); } }, 'Cancel'),
          ok,
        ];
      },
    });
  });
}

// Runs an action; when the switch wants a password, asks for it and retries once.
export async function withSwitchAuth(mac, name, action) {
  try {
    return await action();
  } catch (err) {
    if (!(err instanceof ApiError) || !['password_required', 'switch_auth'].includes(err.code)) throw err;
    const reason = err.code === 'switch_auth' ? `${err.message} Enter the password again.` : null;
    const ok = await switchLoginDialog(mac, name, reason);
    if (!ok) throw new ApiError(0, { code: 'cancelled', message: 'Cancelled.' });
    return action();
  }
}

export function section(title, ...children) {
  return h('section', { class: 'card' }, h('h3', {}, title), ...children);
}

export function unsupported(what) {
  return h('p', { class: 'muted', 'data-testid': 'unsupported' }, `${what} is not supported by this switch.`);
}

export function field(label, control, hint) {
  const id = control.id || `f${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h('div', { class: 'field' }, h('label', { for: id }, label), control, hint ? h('small', { class: 'muted' }, hint) : null);
}

export function select(options, value, attrs = {}) {
  const el = h('select', attrs);
  for (const [val, text] of options) el.append(h('option', { value: val, selected: String(val) === String(value) }, text));
  return el;
}

export async function run(button, fn) {
  button.disabled = true;
  try {
    return await fn();
  } catch (err) {
    if (!(err instanceof ApiError && err.code === 'cancelled')) showError(err);
    return undefined;
  } finally {
    button.disabled = false;
  }
}

// Copy text for a bug report. The clipboard API only exists on HTTPS (or localhost); on a plain
// HTTP LAN address fall back to execCommand, and if that fails too show the text to select.
export async function copyText(text, filename = 'switchyard-report.txt') {
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      toast('Copied.');
      return 'clipboard';
    } catch (err) { /* fall through */ }
  }
  const area = h('textarea', { readonly: true, rows: '10', 'aria-label': 'Report text', 'data-testid': 'copy-text' });
  area.value = text;
  document.body.append(area);
  area.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (err) { copied = false; }
  area.remove();
  if (copied) {
    toast('Copied.');
    return 'execCommand';
  }
  modal({
    title: 'Copy this text',
    body: h('div', {}, h('p', { class: 'muted' }, 'Your browser does not allow copying from this page (it is not served over HTTPS). Select the text and copy it, or download it.'), area),
    actions: (close) => [
      h('button', { class: 'btn', 'data-testid': 'copy-download', onclick: () => download(filename, text, 'text/plain') }, 'Download'),
      h('button', { class: 'btn primary', onclick: close }, 'Close'),
    ],
  });
  area.focus();
  area.select();
  return 'dialog';
}
