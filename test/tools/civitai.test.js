import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as civitai from '../../src/tools/civitai.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  civitai.register(server);
});

describe('GET tools', () => {
  it('passes a query object when args are given', async () => {
    client.get.mockResolvedValue({ items: [] });
    await callTool(server, 'sdnext_civitai_search', { query: 'pony', limit: 5 });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v2/civitai/search', { query: { query: 'pony', limit: 5 } });
  });

  it('passes query: undefined for a schema-less GET tool called with no args', async () => {
    client.get.mockResolvedValue({ username: 'me' });
    const res = await callTool(server, 'sdnext_civitai_me', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v2/civitai/me', { query: undefined });
    expect(resultJson(res).username).toBe('me');
  });
});

describe('POST tools', () => {
  it('sdnext_civitai_download posts the full body', async () => {
    client.post.mockResolvedValue({ download_id: 'abc' });
    const res = await callTool(server, 'sdnext_civitai_download', { url: 'http://civitai/x.safetensors', model_type: 'LoRA' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v2/civitai/download', { body: { url: 'http://civitai/x.safetensors', model_type: 'LoRA' } });
    expect(resultJson(res).download_id).toBe('abc');
  });

  it('sdnext_civitai_clear_history posts with an empty schema', async () => {
    client.post.mockResolvedValue({ cleared: true });
    await callTool(server, 'sdnext_civitai_clear_history', {});
    expect(client.post).toHaveBeenCalledWith('/sdapi/v2/civitai/history', { body: {} });
  });
});

describe('sdnext_civitai_legacy', () => {
  it('gets the legacy v1 endpoint with query args', async () => {
    client.get.mockResolvedValue({ results: [] });
    await callTool(server, 'sdnext_civitai_legacy', { query: 'anime', exact: false });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/civitai', { query: { query: 'anime', exact: false } });
  });
});
