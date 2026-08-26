import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as meta from '../../src/tools/meta.js';
import * as generation from '../../src/tools/generation.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  meta.register(server);
});

describe('sdnext_list_tools', () => {
  it('lists tools registered elsewhere in the process, in addition to its own', async () => {
    generation.register(server); // registers into the shared registry singleton
    const res = await callTool(server, 'sdnext_list_tools', {});
    const body = resultJson(res);
    expect(body.tools.some((t) => t.name === 'sdnext_txt2img')).toBe(true);
    expect(body.tools.some((t) => t.name === 'sdnext_call_api')).toBe(true);
  });
});

describe('sdnext_call_api', () => {
  it('routes GET with an absolute path unchanged', async () => {
    client.get.mockResolvedValue({ a: 1 });
    await callTool(server, 'sdnext_call_api', { method: 'GET', path: '/sdapi/v1/samplers' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/samplers', { query: undefined });
  });

  it('prefixes a relative path with /sdapi/v1', async () => {
    client.get.mockResolvedValue({ a: 1 });
    await callTool(server, 'sdnext_call_api', { method: 'GET', path: 'samplers' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/samplers', { query: undefined });
  });

  it('routes DELETE with body and query', async () => {
    client.del.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_call_api', { method: 'DELETE', path: '/sdapi/v1/x', body: { a: 1 }, query: { b: 2 } });
    expect(client.del).toHaveBeenCalledWith('/sdapi/v1/x', { query: { b: 2 }, body: { a: 1 } });
  });

  it('routes POST (and any non-GET/DELETE method) with body and query', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_call_api', { method: 'POST', path: '/sdapi/v1/x', body: { a: 1 } });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/x', { body: { a: 1 }, query: undefined });
  });
});

describe('autocomplete file tools', () => {
  it('sdnext_autocomplete_content URL-encodes the name', async () => {
    client.get.mockResolvedValue('tag list contents');
    await callTool(server, 'sdnext_autocomplete_content', { name: 'a b/c' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/autocomplete/a%20b%2Fc');
  });

  it('sdnext_autocomplete_download posts to the encoded download path', async () => {
    client.post.mockResolvedValue({ status: 'queued' });
    await callTool(server, 'sdnext_autocomplete_download', { name: 'danbooru' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/autocomplete/danbooru/download');
  });

  it('sdnext_autocomplete_delete deletes the encoded path', async () => {
    client.del.mockResolvedValue({ deleted: true });
    await callTool(server, 'sdnext_autocomplete_delete', { name: 'danbooru' });
    expect(client.del).toHaveBeenCalledWith('/sdapi/v1/autocomplete/danbooru');
  });
});

describe('sdnext_session_start', () => {
  it('passes agent through clean() when given', async () => {
    client.get.mockResolvedValue({ session: 'x' });
    await callTool(server, 'sdnext_session_start', { agent: 'claude' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/start', { query: { agent: 'claude' } });
  });

  it('drops an undefined agent via clean()', async () => {
    client.get.mockResolvedValue({ session: 'x' });
    await callTool(server, 'sdnext_session_start', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/start', { query: {} });
  });
});
