import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as models from '../../src/tools/models.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  models.register(server);
});

describe('post() helper (schema-less vs. schema-bearing)', () => {
  it('sdnext_refresh_checkpoints (no schema) posts with body: undefined', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_refresh_checkpoints', {});
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/refresh-checkpoints', { body: undefined });
  });

  it('sdnext_reload_checkpoint (has schema) posts the given args as the body', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_reload_checkpoint', { force: true });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/reload-checkpoint', { body: { force: true } });
  });
});

describe('checkpoint selection', () => {
  it('sdnext_select_checkpoint posts the checkpoint name', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_select_checkpoint', { checkpoint: 'sd_xl' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/checkpoint', { body: { checkpoint: 'sd_xl' } });
  });

  it('sdnext_get_checkpoint gets the active checkpoint', async () => {
    client.get.mockResolvedValue({ name: 'sd_xl' });
    const res = await callTool(server, 'sdnext_get_checkpoint', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/checkpoint');
    expect(resultJson(res).name).toBe('sd_xl');
  });
});

describe('options', () => {
  it('sdnext_get_options gets all options', async () => {
    client.get.mockResolvedValue({ some_option: 1 });
    await callTool(server, 'sdnext_get_options', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/options');
  });

  it('sdnext_set_options posts the option map', async () => {
    client.post.mockResolvedValue({ ok: true });
    await callTool(server, 'sdnext_set_options', { options: { some_option: 2 } });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/options', { body: { options: { some_option: 2 } } });
  });
});
