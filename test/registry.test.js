import { describe, it, expect, beforeEach, vi } from 'vitest';
import { registerTool, ok, fail, listTools, callApiSchema } from '../src/registry.js';

function fakeServer() {
  const calls = new Map();
  return {
    registerTool: vi.fn((name, cfg, handler) => calls.set(name, { cfg, handler })),
    _calls: calls,
  };
}

describe('ok()', () => {
  it('passes a string through as text', () => {
    expect(ok('plain text')).toEqual({ content: [{ type: 'text', text: 'plain text' }] });
  });

  it('JSON-stringifies non-string data', () => {
    const res = ok({ a: 1 });
    expect(res.content[0].text).toBe(JSON.stringify({ a: 1 }, null, 2));
  });
});

describe('fail()', () => {
  it('formats an Error with a status code', () => {
    const e = Object.assign(new Error('bad'), { name: 'SdNextError', status: 404 });
    expect(fail(e)).toEqual({ isError: true, content: [{ type: 'text', text: 'SdNextError: bad (HTTP 404)' }] });
  });

  it('formats an Error without a status code', () => {
    const e = new Error('boom');
    expect(fail(e).content[0].text).toBe('Error: boom');
  });

  it('falls back to "Error" when the thrown value has no name', () => {
    const e = { message: 'plain object error' };
    expect(fail(e).content[0].text).toBe('Error: plain object error');
  });
});

describe('registerTool()', () => {
  let server;
  beforeEach(() => {
    server = fakeServer();
  });

  it('registers the tool on the server and returns ok() on success', async () => {
    registerTool(server, {
      name: 'sdnext_test_ok',
      description: 'desc',
      inputSchema: {},
      handler: async () => ({ hello: 'world' }),
    });
    expect(server.registerTool).toHaveBeenCalledWith('sdnext_test_ok', expect.objectContaining({ description: 'desc' }), expect.any(Function));
    const { handler } = server._calls.get('sdnext_test_ok');
    const res = await handler({}, {});
    expect(res.isError).toBeUndefined();
    expect(JSON.parse(res.content[0].text)).toEqual({ hello: 'world' });
  });

  it('returns fail() when the handler throws', async () => {
    registerTool(server, {
      name: 'sdnext_test_fail',
      description: 'desc',
      inputSchema: {},
      handler: async () => {
        throw new Error('handler exploded');
      },
    });
    const { handler } = server._calls.get('sdnext_test_fail');
    const res = await handler({}, {});
    expect(res.isError).toBe(true);
    expect(res.content[0].text).toContain('handler exploded');
  });

  it('passes annotations through when provided', () => {
    registerTool(server, {
      name: 'sdnext_test_annotated',
      description: 'desc',
      inputSchema: {},
      annotations: { readOnlyHint: false, destructiveHint: true },
      handler: async () => ({}),
    });
    const [, cfg] = server.registerTool.mock.calls.find(([n]) => n === 'sdnext_test_annotated');
    expect(cfg.annotations).toEqual({ readOnlyHint: false, destructiveHint: true });
  });

  it('omits annotations when not provided', () => {
    registerTool(server, {
      name: 'sdnext_test_unannotated',
      description: 'desc',
      inputSchema: {},
      handler: async () => ({}),
    });
    const [, cfg] = server.registerTool.mock.calls.find(([n]) => n === 'sdnext_test_unannotated');
    expect(cfg.annotations).toBeUndefined();
  });

  it('defaults method to POST and destructive to false in the registry listing', () => {
    registerTool(server, {
      name: 'sdnext_test_defaults',
      description: 'desc',
      inputSchema: {},
      path: '/sdapi/v1/thing',
      handler: async () => ({}),
    });
    const entry = listTools().find((t) => t.name === 'sdnext_test_defaults');
    expect(entry.method).toBe('POST');
    expect(entry.destructive).toBe(false);
  });

  it('records an explicit method and destructive flag in the registry listing', () => {
    registerTool(server, {
      name: 'sdnext_test_explicit',
      description: 'desc',
      inputSchema: {},
      path: '/sdapi/v1/thing',
      method: 'DELETE',
      destructive: true,
      handler: async () => ({}),
    });
    const entry = listTools().find((t) => t.name === 'sdnext_test_explicit');
    expect(entry.method).toBe('DELETE');
    expect(entry.destructive).toBe(true);
  });
});

describe('listTools()', () => {
  it('returns tools sorted by name', () => {
    const server = fakeServer();
    registerTool(server, { name: 'sdnext_zzz', description: 'd', inputSchema: {}, handler: async () => ({}) });
    registerTool(server, { name: 'sdnext_aaa', description: 'd', inputSchema: {}, handler: async () => ({}) });
    const names = listTools().map((t) => t.name);
    const idxA = names.indexOf('sdnext_aaa');
    const idxZ = names.indexOf('sdnext_zzz');
    expect(idxA).toBeLessThan(idxZ);
  });
});

describe('callApiSchema', () => {
  it('defaults method to GET and accepts a bare path', () => {
    const parsed = callApiSchema.parse({ path: 'samplers' });
    expect(parsed.method).toBe('GET');
  });

  it('accepts an explicit method, body, and query', () => {
    const parsed = callApiSchema.parse({ method: 'POST', path: '/sdapi/v1/x', body: { a: 1 }, query: { b: 'c' } });
    expect(parsed.method).toBe('POST');
    expect(parsed.body).toEqual({ a: 1 });
  });
});
