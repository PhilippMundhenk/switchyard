// Thin fetch wrapper. Errors carry the server's code and message.

export class ApiError extends Error {
  constructor(status, body) {
    super(body.message || 'Request failed');
    this.status = status;
    this.code = body.code || 'error';
    this.body = body;
  }
}

const listeners = { unauthorized: [] };
export function onUnauthorized(fn) {
  listeners.unauthorized.push(fn);
}

function validationMessage(body) {
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((d) => `${(d.loc || []).slice(1).join('.') || 'value'}: ${d.msg}`)
      .join('; ');
  }
  return null;
}

export async function api(method, path, body) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(0, { code: 'network', message: 'Cannot reach the Switchyard server.' });
  }
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    data = {};
  }
  if (!response.ok) {
    const message = validationMessage(data);
    const error = new ApiError(response.status, message ? { code: 'invalid', message } : data);
    if (response.status === 401 && error.code === 'login_required') {
      listeners.unauthorized.forEach((fn) => fn());
    }
    throw error;
  }
  return data;
}

export const get = (path) => api('GET', path);
export const post = (path, body = {}) => api('POST', path, body);
export const put = (path, body) => api('PUT', path, body);
export const patch = (path, body) => api('PATCH', path, body);
export const del = (path) => api('DELETE', path);

export const dev = (mac) => `/devices/${encodeURIComponent(mac)}`;
