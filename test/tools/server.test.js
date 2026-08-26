import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as serverTools from '../../src/tools/server.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  serverTools.register(server);
});

describe('GET tools (query present vs. empty)', () => {
  it('passes a query object when args are given', async () => {
    client.get.mockResolvedValue({ progress: 0.5 });
    await callTool(server, 'sdnext_progress', { skip_current_image: true });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/progress', { query: { skip_current_image: true } });
  });

  it('passes query: undefined for a schema-less GET tool called with no args', async () => {
    client.get.mockResolvedValue({ state: 'idle' });
    const res = await callTool(server, 'sdnext_status', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/status', { query: undefined });
    expect(resultJson(res).state).toBe('idle');
  });
});

describe('POST tools (destructive vs. non-destructive, body present vs. empty)', () => {
  it('sdnext_interrupt (non-destructive, no schema) posts with body: undefined', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_interrupt', {});
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/interrupt', { body: undefined });
  });

  it('sdnext_restart is registered with destructive annotations', async () => {
    const { config: cfg } = server._tools.get('sdnext_restart');
    expect(cfg.annotations).toEqual({ readOnlyHint: false, destructiveHint: true });
  });

  it('sdnext_status (non-destructive) has no annotations set', async () => {
    const { config: cfg } = server._tools.get('sdnext_status');
    expect(cfg.annotations).toBeUndefined();
  });

  it('sdnext_log_write (schema present) posts the given body', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_log_write', { message: 'hello' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/log', { body: { message: 'hello' } });
  });
});
