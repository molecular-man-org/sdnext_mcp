import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});
vi.mock('../../src/image.js', () => ({ saveImages: vi.fn() }));

import { client } from '../../src/client.js';
import { saveImages } from '../../src/image.js';
import * as generation from '../../src/tools/generation.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  generation.register(server);
});

describe('sdnext_txt2img / sdnext_img2img / sdnext_control (shapeImages)', () => {
  it('returns images, saved_files, info and parameters when the API returns a full payload', async () => {
    client.post.mockResolvedValue({ images: ['aaa', 'bbb'], info: 'i', parameters: { steps: 20 } });
    saveImages.mockReturnValue(['/out/a.png', '/out/b.png']);

    const res = await callTool(server, 'sdnext_txt2img', { prompt: 'cat', save_dir: '/out' });
    const body = resultJson(res);

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/txt2img', { body: { prompt: 'cat', save_dir: '/out' } });
    expect(saveImages).toHaveBeenCalledWith(['aaa', 'bbb'], { dir: '/out' });
    expect(body.images).toEqual(['aaa', 'bbb']);
    expect(body.saved_files).toEqual(['/out/a.png', '/out/b.png']);
    expect(body.info).toBe('i');
    expect(body.parameters).toEqual({ steps: 20 });
  });

  it('defaults images to [] and omits saved_files when nothing was written', async () => {
    client.post.mockResolvedValue({});
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_img2img', { prompt: 'dog' });
    const body = resultJson(res);

    expect(body.images).toEqual([]);
    expect(body.saved_files).toBeUndefined();
  });

  it('handles a nullish API response (data ?? {} branch)', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_control', { control_units: [] });
    const body = resultJson(res);

    expect(body.images).toEqual([]);
    expect(body.info).toBeUndefined();
    expect(body.parameters).toBeUndefined();
  });
});

describe('sdnext_process_image', () => {
  it('saves the processed image when save_dir is given', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', html_info: 'x' });
    saveImages.mockReturnValue(['/out/p.png']);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in', save_dir: '/out' });
    const body = resultJson(res);

    expect(saveImages).toHaveBeenCalledWith(['imgdata'], { dir: '/out' });
    expect(body.image).toBe('imgdata');
    expect(body.saved_files).toEqual(['/out/p.png']);
  });

  it('does not save when save_dir is omitted, even if an image comes back', async () => {
    client.post.mockResolvedValue({ image: 'imgdata' });

    const res = await callTool(server, 'sdnext_process_image', { image: 'in' });
    const body = resultJson(res);

    expect(saveImages).not.toHaveBeenCalled();
    expect(body.image).toBe('imgdata');
    expect(body.saved_files).toBeUndefined();
  });

  it('skips the image branch entirely when the API returns no image', async () => {
    client.post.mockResolvedValue({ html_info: 'no image' });

    const res = await callTool(server, 'sdnext_process_image', { image: 'in', save_dir: '/out' });
    const body = resultJson(res);

    expect(saveImages).not.toHaveBeenCalled();
    expect(body.image).toBeUndefined();
  });
});

describe('sdnext_process_batch', () => {
  it('saves all returned images when save_dir is given and images is an array', async () => {
    client.post.mockResolvedValue({ images: ['a', 'b'] });
    saveImages.mockReturnValue(['/out/a.png', '/out/b.png']);

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1', 'in2'], save_dir: '/out' });
    const body = resultJson(res);

    expect(saveImages).toHaveBeenCalledWith(['a', 'b'], { dir: '/out' });
    expect(body.saved_files).toEqual(['/out/a.png', '/out/b.png']);
  });

  it('does not save when save_dir is omitted', async () => {
    client.post.mockResolvedValue({ images: ['a', 'b'] });

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1'] });
    const body = resultJson(res);

    expect(saveImages).not.toHaveBeenCalled();
    expect(body.saved_files).toBeUndefined();
  });

  it('does not save when save_dir is given but the API did not return an images array', async () => {
    client.post.mockResolvedValue({ images: 'not-an-array' });

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1'], save_dir: '/out' });
    const body = resultJson(res);

    expect(saveImages).not.toHaveBeenCalled();
    expect(body.saved_files).toBeUndefined();
  });
});

describe('sdnext_detail', () => {
  it('saves image and detections (filtering out the falsy one) when save_dir is given', async () => {
    client.post.mockResolvedValue({ image: 'imgdata' }); // detections absent -> filtered out
    saveImages.mockReturnValue(['/out/d.png']);

    const res = await callTool(server, 'sdnext_detail', { image: 'in', save_dir: '/out' });
    resultJson(res);

    expect(saveImages).toHaveBeenCalledWith(['imgdata'], { dir: '/out' });
  });

  it('saves both image and detections when both are present', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', detections: 'detdata' });
    saveImages.mockReturnValue(['/out/d.png', '/out/det.png']);

    await callTool(server, 'sdnext_detail', { image: 'in', save_dir: '/out' });

    expect(saveImages).toHaveBeenCalledWith(['imgdata', 'detdata'], { dir: '/out' });
  });

  it('does not save when save_dir is omitted', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', detections: 'detdata' });

    await callTool(server, 'sdnext_detail', { image: 'in' });

    expect(saveImages).not.toHaveBeenCalled();
  });
});

describe('sdnext_prompt_enhance', () => {
  it('passes the cleaned args straight through to the API', async () => {
    client.post.mockResolvedValue({ prompt: 'enhanced' });

    const res = await callTool(server, 'sdnext_prompt_enhance', { prompt: 'cat', model: undefined });
    const body = resultJson(res);

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/prompt-enhance', { body: { prompt: 'cat' } });
    expect(body.prompt).toBe('enhanced');
  });
});
