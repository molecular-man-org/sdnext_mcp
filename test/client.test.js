import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { apiUrl, clean, SdNextError, client } from '../src/client.js';
import { config } from '../src/config.js';

const ORIGINAL = { baseUrl: config.baseUrl, apiPath: config.apiPath, dryRun: config.dryRun };

beforeEach(() => {
  config.baseUrl = 'http://127.0.0.1:7860';
  config.apiPath = '/sdapi/v1';
  config.dryRun = false;
  config.apiKey = '';
  config.username = '';
  config.password = '';
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  Object.assign(config, ORIGINAL);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body, { ok = true, status = 200, statusText = 'OK' } = {}) {
  return {
    ok,
    status,
    statusText,
    text: async () => JSON.stringify(body),
  };
}

describe('apiUrl()', () => {
  it('passes absolute http(s) URLs through unchanged', () => {
    expect(apiUrl('http://other-host/x')).toBe('http://other-host/x');
    expect(apiUrl('https://other-host/x')).toBe('https://other-host/x');
  });

  it('prefixes an absolute path with baseUrl only', () => {
    expect(apiUrl('/foo/bar')).toBe('http://127.0.0.1:7860/foo/bar');
  });

  it('prefixes a bare relative path with baseUrl + apiPath', () => {
    expect(apiUrl('samplers')).toBe('http://127.0.0.1:7860/sdapi/v1/samplers');
  });
});

describe('clean()', () => {
  it('returns null as-is', () => {
    expect(clean(null)).toBeNull();
  });

  it('returns non-object primitives as-is', () => {
    expect(clean('str')).toBe('str');
    expect(clean(42)).toBe(42);
  });

  it('drops undefined and null fields but keeps others', () => {
    expect(clean({ a: 1, b: undefined, c: null, d: 0, e: false })).toEqual({ a: 1, d: 0, e: false });
  });
});

describe('client.get/post/put/del via request()', () => {
  it('returns a dry-run descriptor without calling fetch when config.dryRun is true', async () => {
    config.dryRun = true;
    const res = await client.post('/sdapi/v1/txt2img', { body: { prompt: 'a cat', skip: undefined } });
    expect(res.__dryRun).toBe(true);
    expect(res.method).toBe('POST');
    expect(res.body).toEqual({ prompt: 'a cat' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('performs a GET with query params and parses a JSON response', async () => {
    fetch.mockResolvedValue(jsonResponse({ samplers: ['euler_a'] }));
    const res = await client.get('/sdapi/v1/samplers', { query: { a: 1, b: undefined, c: null } });
    expect(res).toEqual({ samplers: ['euler_a'] });
    const [url] = fetch.mock.calls[0];
    expect(url.toString()).toBe('http://127.0.0.1:7860/sdapi/v1/samplers?a=1');
  });

  it('performs a POST with a JSON body and Content-Type header', async () => {
    fetch.mockResolvedValue(jsonResponse({ ok: true }));
    await client.post('/sdapi/v1/txt2img', { body: { prompt: 'cat' } });
    const [, opts] = fetch.mock.calls[0];
    expect(opts.method).toBe('POST');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(opts.body).toBe(JSON.stringify({ prompt: 'cat' }));
  });

  it('performs a PUT with a JSON body', async () => {
    fetch.mockResolvedValue(jsonResponse({ ok: true }));
    await client.put('/sdapi/v1/thing', { body: { x: 1 } });
    const [, opts] = fetch.mock.calls[0];
    expect(opts.method).toBe('PUT');
    expect(opts.headers['Content-Type']).toBe('application/json');
  });

  it('performs a DELETE without a body', async () => {
    fetch.mockResolvedValue(jsonResponse({ ok: true }));
    await client.del('/sdapi/v1/delete-file', { query: { file: 'a.png' } });
    const [, opts] = fetch.mock.calls[0];
    expect(opts.method).toBe('DELETE');
    expect(opts.headers['Content-Type']).toBeUndefined();
  });

  it('handles an empty response body as {}', async () => {
    fetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', text: async () => '' });
    const res = await client.get('/sdapi/v1/interrupt');
    expect(res).toEqual({});
  });

  it('falls back to a raw-text wrapper when the body is not valid JSON', async () => {
    fetch.mockResolvedValue({ ok: true, status: 200, statusText: 'OK', text: async () => 'not json {' });
    const res = await client.get('/sdapi/v1/log');
    expect(res).toEqual({ raw: 'not json {' });
  });

  it('throws SdNextError with detail from data.detail on non-ok response', async () => {
    fetch.mockResolvedValue(jsonResponse({ detail: 'bad request' }, { ok: false, status: 400, statusText: 'Bad Request' }));
    await expect(client.get('/sdapi/v1/x')).rejects.toMatchObject({
      name: 'SdNextError',
      status: 400,
    });
  });

  it('throws SdNextError with detail from data.error when data.detail is absent', async () => {
    fetch.mockResolvedValue(jsonResponse({ error: 'boom' }, { ok: false, status: 500, statusText: 'Server Error' }));
    await expect(client.get('/sdapi/v1/x')).rejects.toThrow(/boom/);
  });

  it('falls back to raw text when neither detail nor error is present', async () => {
    fetch.mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Unavailable',
      text: async () => JSON.stringify({ other: 'nope' }),
    });
    await expect(client.get('/sdapi/v1/x')).rejects.toThrow(/other/);
  });

  it('stringifies a non-string detail object', async () => {
    fetch.mockResolvedValue(jsonResponse({ detail: { field: 'prompt', msg: 'required' } }, { ok: false, status: 422, statusText: 'Unprocessable' }));
    await expect(client.get('/sdapi/v1/x')).rejects.toThrow(/"field":"prompt"/);
  });

  it('re-throws SdNextError instances unchanged (does not double-wrap)', async () => {
    fetch.mockResolvedValue(jsonResponse({ detail: 'nope' }, { ok: false, status: 400, statusText: 'Bad' }));
    try {
      await client.get('/sdapi/v1/x');
      throw new Error('expected client.get to throw');
    } catch (e) {
      expect(e).toBeInstanceOf(SdNextError);
    }
  });

  it('wraps an AbortError as a timeout SdNextError', async () => {
    const abortErr = new Error('The operation was aborted');
    abortErr.name = 'AbortError';
    fetch.mockRejectedValue(abortErr);
    await expect(client.get('/sdapi/v1/x', { timeoutMs: 10 })).rejects.toThrow(/timed out/);
  });

  it('wraps a generic network error as an unreachable SdNextError', async () => {
    fetch.mockRejectedValue(new Error('ECONNREFUSED'));
    await expect(client.get('/sdapi/v1/x')).rejects.toThrow(/Cannot reach SD.Next/);
  });

  it('sends Authorization header from authHeader()', async () => {
    config.apiKey = 'tok';
    fetch.mockResolvedValue(jsonResponse({ ok: true }));
    await client.get('/sdapi/v1/status');
    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe('Bearer tok');
  });
});
