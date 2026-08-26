import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as moderation from '../../src/tools/moderation.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  moderation.register(server);
});

describe('sdnext_nudenet', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ censored_image: 'out' });
    const res = await callTool(server, 'sdnext_nudenet', { image: 'in', method: 'blur' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/nudenet', { body: { image: 'in', method: 'blur' } });
    expect(resultJson(res).censored_image).toBe('out');
  });
});

describe('sdnext_prompt_lang', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ allowed: true });
    await callTool(server, 'sdnext_prompt_lang', { prompt: 'a cat' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/prompt-lang', { body: { prompt: 'a cat' } });
  });
});

describe('sdnext_image_guard', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ safe: true });
    await callTool(server, 'sdnext_image_guard', { image: 'in' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/image-guard', { body: { image: 'in' } });
  });
});

describe('sdnext_prompt_banned', () => {
  it('posts the args as the body', async () => {
    client.post.mockResolvedValue({ matched: [] });
    await callTool(server, 'sdnext_prompt_banned', { prompt: 'a cat' });
    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/prompt-banned', { body: { prompt: 'a cat' } });
  });
});
