// sdnext_mcp/src/client.js — SD.Next HTTP 客户端
// 认证: SD.Next 使用 HTTP Basic Auth (api_auth=user:pass)。
// bearer token 可放在 SDNEXT_API_KEY（作为 Basic 的 password，username 任意）。
// 见 framework/01-sdnext-api/auth.md

import { config, authHeader } from './config.js';

export function apiUrl(path) {
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  if (path.startsWith('/')) return `${config.baseUrl}${path}`;
  return `${config.baseUrl}${config.apiPath}/${path.replace(/^\/+/, '')}`;
}

// 剔除 undefined/null（zod .optional() 未填时），SD.Next pydantic 模型对缺失字段取默认值
export function clean(obj) {
  if (obj === null || typeof obj !== 'object') return obj;
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    out[k] = v;
  }
  return out;
}

export class SdNextError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = 'SdNextError';
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = 'GET', body, query, headers = {}, timeoutMs } = {}) {
  const url = new URL(apiUrl(path));
  if (query) for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    url.searchParams.set(k, String(v));
  }

  if (config.dryRun) {
    return {
      __dryRun: true,
      method,
      url: url.toString(),
      body: body === undefined ? undefined : clean(body),
      note: 'DRY RUN (SDNEXT_MCP_DRY_RUN=1): request not sent',
    };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs ?? config.timeoutMs);
  const finalHeaders = { ...authHeader(), ...headers };
  let payload;
  try {
    if (body !== undefined && (method === 'POST' || method === 'PUT')) {
      finalHeaders['Content-Type'] = 'application/json';
      payload = JSON.stringify(clean(body));
    }
    const res = await fetch(url, { method, headers: finalHeaders, body: payload, signal: controller.signal });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch { data = { raw: text.slice(0, 4000) }; }
    if (!res.ok) {
      const detail = (data && (data.detail || data.error)) || text.slice(0, 1000);
      throw new SdNextError(`SD.Next API ${res.status} ${res.statusText} for ${method} ${url.pathname}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`, { status: res.status, body: data });
    }
    return data;
  } catch (e) {
    if (e instanceof SdNextError) throw e;
    if (e.name === 'AbortError') throw new SdNextError(`Request timed out after ${timeoutMs ?? config.timeoutMs}ms: ${method} ${url.pathname}`);
    throw new SdNextError(`Cannot reach SD.Next at ${url.origin} — is the WebUI running with --api --listen? (${e.message})`);
  } finally {
    clearTimeout(timer);
  }
}

export const client = {
  get: (path, opts) => request(path, { ...opts, method: 'GET' }),
  post: (path, opts) => request(path, { ...opts, method: 'POST' }),
  put: (path, opts) => request(path, { ...opts, method: 'PUT' }),
  del: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
  getBinary,
};

// For endpoints that serve a raw file (e.g. GET /sdapi/v1/video/file) rather
// than JSON — request()'s res.text() would corrupt binary content. Returns
// { base64 } on success, or the same __dryRun descriptor shape as request().
async function getBinary(path, { query, timeoutMs } = {}) {
  const url = new URL(apiUrl(path));
  if (query) for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    url.searchParams.set(k, String(v));
  }

  if (config.dryRun) {
    return {
      __dryRun: true,
      method: 'GET',
      url: url.toString(),
      note: 'DRY RUN (SDNEXT_MCP_DRY_RUN=1): request not sent',
    };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs ?? config.timeoutMs);
  try {
    const res = await fetch(url, { method: 'GET', headers: authHeader(), signal: controller.signal });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new SdNextError(`SD.Next API ${res.status} ${res.statusText} for GET ${url.pathname}: ${text.slice(0, 1000)}`, { status: res.status });
    }
    const buf = Buffer.from(await res.arrayBuffer());
    return { base64: buf.toString('base64') };
  } catch (e) {
    if (e instanceof SdNextError) throw e;
    if (e.name === 'AbortError') throw new SdNextError(`Request timed out after ${timeoutMs ?? config.timeoutMs}ms: GET ${url.pathname}`);
    throw new SdNextError(`Cannot reach SD.Next at ${url.origin} — is the WebUI running with --api --listen? (${e.message})`);
  } finally {
    clearTimeout(timer);
  }
}
