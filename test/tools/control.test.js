import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as control from '../../src/tools/control.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  control.register(server);
});

describe('sdnext_preprocess', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ image: 'edge-map' });
    const res = await callTool(server, 'sdnext_preprocess', { image: 'in', model: 'canny' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/preprocess', { body: { image: 'in', model: 'canny' } });
    expect(resultJson(res).image).toBe('edge-map');
  });
});

describe('sdnext_mask', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ mask: 'm' });
    await callTool(server, 'sdnext_mask', { image: 'in', type: 'mask' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/mask', { body: { image: 'in', type: 'mask' } });
  });
});

describe('sdnext_detect', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ boxes: [] });
    await callTool(server, 'sdnext_detect', { image: 'in' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/detect', { body: { image: 'in' } });
  });
});
