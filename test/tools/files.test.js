import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import { config } from '../../src/config.js';
import * as files from '../../src/tools/files.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn());
  config.dryRun = false;
  config.apiKey = '';
  config.username = '';
  config.password = '';
  config.baseUrl = 'http://127.0.0.1:7860';
  server = fakeServer();
  files.register(server);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('GET-based tools (query present vs. empty)', () => {
  it('passes a query object when args are given', async () => {
    client.get.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_file', { file: 'outputs/a.png' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/file', { query: { file: 'outputs/a.png' } });
  });

  it('passes query: undefined when called with no meaningful args', async () => {
    client.get.mockResolvedValue([]);
    await callTool(server, 'sdnext_browser_folders', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/browser/folders', { query: undefined });
  });
});

describe('DELETE-based tools (destructive)', () => {
  it('sdnext_delete_image sends the file as a query param', async () => {
    client.del.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_delete_image', { file: 'outputs/a.png' });
    expect(client.del).toHaveBeenCalledWith('/sdapi/v1/delete-image', { query: { file: 'outputs/a.png' } });
  });

  it('sdnext_delete_file sends the file as a query param', async () => {
    client.del.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_delete_file', { file: 'models/x.safetensors' });
    expect(client.del).toHaveBeenCalledWith('/sdapi/v1/delete-file', { query: { file: 'models/x.safetensors' } });
  });
});

describe('sdnext_png_info', () => {
  it('posts the image body', async () => {
    client.post.mockResolvedValue({ info: 'params here' });
    const res = await callTool(server, 'sdnext_png_info', { image: 'b64' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/png-info', { body: { image: 'b64' } });
    expect(resultJson(res).info).toBe('params here');
  });
});

describe('sdnext_upload', () => {
  it('returns a dry-run descriptor without calling fetch when config.dryRun is true', async () => {
    config.dryRun = true;
    const res = await callTool(server, 'sdnext_upload', {
      filename: 'x.png',
      content_base64: Buffer.from('hello').toString('base64'),
      overwrite: true,
      path: 'sub',
    });
    const body = resultJson(res);
    expect(body.__dryRun).toBe(true);
    expect(body.form.overwrite).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('uploads via multipart form (overwrite=true) and returns the parsed JSON response', async () => {
    fetch.mockResolvedValue({ ok: true, text: async () => JSON.stringify({ uploaded: 'x.png' }) });
    const res = await callTool(server, 'sdnext_upload', {
      filename: 'x.png',
      content_base64: Buffer.from('hello').toString('base64'),
      overwrite: true,
    });
    expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:7860/sdapi/v1/upload', expect.objectContaining({ method: 'POST' }));
    expect(resultJson(res).uploaded).toBe('x.png');
  });

  it('treats an empty response body as {} rather than parsing it', async () => {
    fetch.mockResolvedValue({ ok: true, text: async () => '' });
    const res = await callTool(server, 'sdnext_upload', { filename: 'x.png', content_base64: 'aGVsbG8=' });
    expect(resultJson(res)).toEqual({});
  });

  it('falls back to a raw-text wrapper when the response body is not valid JSON', async () => {
    fetch.mockResolvedValue({ ok: true, text: async () => 'not json' });
    const res = await callTool(server, 'sdnext_upload', { filename: 'x.png', content_base64: 'aGVsbG8=' });
    expect(resultJson(res).raw).toBe('not json');
  });

  it('throws with detail from data.detail when the upload fails', async () => {
    fetch.mockResolvedValue({ ok: false, status: 413, text: async () => JSON.stringify({ detail: 'too large' }) });
    const res = await callTool(server, 'sdnext_upload', { filename: 'x.png', content_base64: 'aGVsbG8=' });
    expect(res.isError).toBe(true);
    expect(res.content[0].text).toContain('too large');
  });

  it('stringifies a non-string detail object', async () => {
    fetch.mockResolvedValue({ ok: false, status: 422, text: async () => JSON.stringify({ detail: { field: 'filename' } }) });
    const res = await callTool(server, 'sdnext_upload', { filename: 'x.png', content_base64: 'aGVsbG8=' });
    expect(res.isError).toBe(true);
    expect(res.content[0].text).toContain('"field":"filename"');
  });

  it('falls back to raw text when the failed response has no detail', async () => {
    fetch.mockResolvedValue({ ok: false, status: 500, text: async () => 'server exploded' });
    const res = await callTool(server, 'sdnext_upload', { filename: 'x.png', content_base64: 'aGVsbG8=' });
    expect(res.isError).toBe(true);
    expect(res.content[0].text).toContain('server exploded');
  });
});
