import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as caption from '../../src/tools/caption.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  caption.register(server);
});

describe('sdnext_caption', () => {
  it('posts to /sdapi/v1/openclip with the args as the body', async () => {
    client.post.mockResolvedValue({ caption: 'a cat' });
    const res = await callTool(server, 'sdnext_caption', { image: 'in', mode: 'best' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/openclip', { body: { image: 'in', mode: 'best' } });
    expect(resultJson(res).caption).toBe('a cat');
  });
});

describe('sdnext_caption_dispatch', () => {
  it('posts the backend-specific args to /sdapi/v1/caption', async () => {
    client.post.mockResolvedValue({ tags: ['1girl'] });
    await callTool(server, 'sdnext_caption_dispatch', { backend: 'tagger', image: 'in', threshold: 0.5 });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/caption', { body: { backend: 'tagger', image: 'in', threshold: 0.5 } });
  });
});

describe('sdnext_tagger', () => {
  it('posts to /sdapi/v1/tagger', async () => {
    client.post.mockResolvedValue({ tags: [] });
    await callTool(server, 'sdnext_tagger', { image: 'in' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/tagger', { body: { image: 'in' } });
  });
});

describe('sdnext_vqa', () => {
  it('posts to /sdapi/v1/vqa', async () => {
    client.post.mockResolvedValue({ answer: 'a dog' });
    await callTool(server, 'sdnext_vqa', { image: 'in', question: 'Short Caption' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/vqa', { body: { image: 'in', question: 'Short Caption' } });
  });
});

describe('sdnext_analyze', () => {
  it('posts to /sdapi/v1/analyze', async () => {
    client.post.mockResolvedValue({ answer: 'analysis' });
    await callTool(server, 'sdnext_analyze', { image: 'in' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/analyze', { body: { image: 'in' } });
  });
});
