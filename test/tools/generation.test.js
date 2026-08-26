import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});
vi.mock('../../src/image.js', () => ({ saveImages: vi.fn() }));

import { client } from '../../src/client.js';
import { config } from '../../src/config.js';
import { saveImages } from '../../src/image.js';
import * as generation from '../../src/tools/generation.js';
import { fakeServer, callTool, resultJson, imageBlocks } from '../harness.js';

let server;
const ORIGINAL_MAX = config.maxImagesInResponse;

beforeEach(() => {
  vi.clearAllMocks();
  config.maxImagesInResponse = ORIGINAL_MAX;
  server = fakeServer();
  generation.register(server);
});

describe('sdnext_txt2img / sdnext_img2img / sdnext_control (shapeImageResult)', () => {
  it('inlines images as MCP image content when no save_dir is given, and forces send_images:true', () => {
    client.post.mockResolvedValue({ images: ['aaa', 'bbb'], info: 'i', parameters: { steps: 20 } });
    saveImages.mockReturnValue([]);

    return callTool(server, 'sdnext_txt2img', { prompt: 'cat' }).then((res) => {
      expect(client.post).toHaveBeenCalledWith('/sdapi/v1/txt2img', { body: { prompt: 'cat', send_images: true } });
      expect(saveImages).toHaveBeenCalledWith(['aaa', 'bbb'], { dir: undefined });
      const body = resultJson(res);
      expect(body.image_count).toBe(2);
      expect(body.info).toBe('i');
      expect(body.parameters).toEqual({ steps: 20 });
      expect(imageBlocks(res)).toEqual([
        { type: 'image', data: 'aaa', mimeType: 'image/png' },
        { type: 'image', data: 'bbb', mimeType: 'image/png' },
      ]);
    });
  });

  it('saves to disk and omits inline images by default when save_dir is given', async () => {
    client.post.mockResolvedValue({ images: ['aaa'], info: 'i' });
    saveImages.mockReturnValue(['/out/a.png']);

    const res = await callTool(server, 'sdnext_img2img', { prompt: 'dog', save_dir: '/out' });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/img2img', { body: { prompt: 'dog', save_dir: '/out', send_images: true } });
    const body = resultJson(res);
    expect(body.saved_files).toEqual(['/out/a.png']);
    expect(body.note).toMatch(/not included inline/);
    expect(imageBlocks(res)).toEqual([]);
  });

  it('return_images:true forces inlining even when save_dir is given', async () => {
    client.post.mockResolvedValue({ images: ['aaa'] });
    saveImages.mockReturnValue(['/out/a.png']);

    const res = await callTool(server, 'sdnext_control', { control_units: [], save_dir: '/out', return_images: true });

    expect(imageBlocks(res)).toEqual([{ type: 'image', data: 'aaa', mimeType: 'image/png' }]);
    expect(resultJson(res).saved_files).toEqual(['/out/a.png']);
  });

  it('return_images:false suppresses inlining even without save_dir', async () => {
    client.post.mockResolvedValue({ images: ['aaa'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_txt2img', { prompt: 'cat', return_images: false });

    expect(imageBlocks(res)).toEqual([]);
    expect(resultJson(res).note).toMatch(/not included inline/);
  });

  it('respects an explicit send_images:false from the caller when bytes are not otherwise needed', async () => {
    client.post.mockResolvedValue({});
    saveImages.mockReturnValue([]);

    await callTool(server, 'sdnext_txt2img', { prompt: 'cat', return_images: false, send_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/txt2img', { body: { prompt: 'cat', send_images: false } });
  });

  it('caps inline images at config.maxImagesInResponse, still saving all of them', async () => {
    config.maxImagesInResponse = 2;
    client.post.mockResolvedValue({ images: ['a', 'b', 'c', 'd'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_txt2img', { prompt: 'cat' });

    expect(saveImages).toHaveBeenCalledWith(['a', 'b', 'c', 'd'], { dir: undefined });
    expect(imageBlocks(res)).toHaveLength(2);
    const body = resultJson(res);
    expect(body.image_count).toBe(4);
    expect(body.note).toMatch(/Showing 2 of 4/);
  });

  it('caps inline images even when save_dir also saved all of them (both notes share the "files were saved" wording)', async () => {
    config.maxImagesInResponse = 1;
    client.post.mockResolvedValue({ images: ['a', 'b'] });
    saveImages.mockReturnValue(['/out/a.png', '/out/b.png']);

    const res = await callTool(server, 'sdnext_txt2img', { prompt: 'cat', save_dir: '/out', return_images: true });

    const body = resultJson(res);
    expect(body.note).toBe('Showing 1 of 2 image(s) inline (SDNEXT_MAX_IMAGES=1).');
  });

  it('img2img: no save_dir + return_images:false does not force send_images and omits inline images', async () => {
    client.post.mockResolvedValue({ images: ['aaa'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_img2img', { prompt: 'dog', return_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/img2img', { body: { prompt: 'dog' } });
    expect(imageBlocks(res)).toEqual([]);
  });

  it('control: no save_dir + return_images:false does not force send_images and omits inline images', async () => {
    client.post.mockResolvedValue({ images: ['aaa'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_control', { control_units: [], return_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/control', { body: { control_units: [] } });
    expect(imageBlocks(res)).toEqual([]);
  });

  it('handles a nullish API response (no images, no info/parameters)', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_control', { control_units: [] });
    const body = resultJson(res);

    expect(body.image_count).toBe(0);
    expect(body.info).toBeUndefined();
    expect(imageBlocks(res)).toEqual([]);
  });
});

describe('sdnext_process_image', () => {
  it('inlines the processed image by default and preserves other response fields', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', html_info: 'x' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in' });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/process', { body: { image: 'in', show_extras_results: true } });
    expect(imageBlocks(res)).toEqual([{ type: 'image', data: 'imgdata', mimeType: 'image/png' }]);
    expect(resultJson(res).html_info).toBe('x');
  });

  it('saves and omits inline output when save_dir is given', async () => {
    client.post.mockResolvedValue({ image: 'imgdata' });
    saveImages.mockReturnValue(['/out/p.png']);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in', save_dir: '/out' });

    expect(saveImages).toHaveBeenCalledWith(['imgdata'], { dir: '/out' });
    expect(imageBlocks(res)).toEqual([]);
    expect(resultJson(res).saved_files).toEqual(['/out/p.png']);
  });

  it('produces an empty image list when the API returns no image', async () => {
    client.post.mockResolvedValue({ html_info: 'no image' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in' });

    expect(imageBlocks(res)).toEqual([]);
    expect(resultJson(res).image_count).toBe(0);
  });

  it('no save_dir + return_images:false does not force show_extras_results', async () => {
    client.post.mockResolvedValue({ image: 'imgdata' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in', return_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/process', { body: { image: 'in' } });
    expect(imageBlocks(res)).toEqual([]);
  });

  it('handles a nullish API response', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_image', { image: 'in' });

    expect(resultJson(res).image_count).toBe(0);
  });
});

describe('sdnext_process_batch', () => {
  it('inlines all batch images by default and forces show_extras_results:true', async () => {
    client.post.mockResolvedValue({ images: ['a', 'b'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1', 'in2'] });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/process-batch', { body: { images: ['in1', 'in2'], show_extras_results: true } });
    expect(imageBlocks(res)).toHaveLength(2);
  });

  it('saves and omits inline images when save_dir is given', async () => {
    client.post.mockResolvedValue({ images: ['a', 'b'] });
    saveImages.mockReturnValue(['/out/a.png', '/out/b.png']);

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1'], save_dir: '/out' });

    expect(saveImages).toHaveBeenCalledWith(['a', 'b'], { dir: '/out' });
    expect(imageBlocks(res)).toEqual([]);
    expect(resultJson(res).saved_files).toEqual(['/out/a.png', '/out/b.png']);
  });

  it('no save_dir + return_images:false does not force show_extras_results', async () => {
    client.post.mockResolvedValue({ images: ['a'] });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1'], return_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/process-batch', { body: { images: ['in1'] } });
    expect(imageBlocks(res)).toEqual([]);
  });

  it('handles a nullish API response', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_process_batch', { images: ['in1'] });

    expect(resultJson(res).image_count).toBe(0);
  });
});

describe('sdnext_detail', () => {
  it('inlines image and detections (filtering the falsy one) by default', async () => {
    client.post.mockResolvedValue({ image: 'imgdata' }); // detections absent -> filtered out
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_detail', { image: 'in' });

    expect(saveImages).toHaveBeenCalledWith(['imgdata'], { dir: undefined });
    expect(imageBlocks(res)).toEqual([{ type: 'image', data: 'imgdata', mimeType: 'image/png' }]);
  });

  it('inlines both image and detections when both are present', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', detections: 'detdata' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_detail', { image: 'in' });

    expect(imageBlocks(res)).toHaveLength(2);
  });

  it('saves and omits inline output when save_dir is given', async () => {
    client.post.mockResolvedValue({ image: 'imgdata', detections: 'detdata' });
    saveImages.mockReturnValue(['/out/d.png', '/out/det.png']);

    const res = await callTool(server, 'sdnext_detail', { image: 'in', save_dir: '/out' });

    expect(saveImages).toHaveBeenCalledWith(['imgdata', 'detdata'], { dir: '/out' });
    expect(imageBlocks(res)).toEqual([]);
  });

  it('handles a nullish API response', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_detail', { image: 'in' });

    expect(resultJson(res).image_count).toBe(0);
  });
});

describe('sdnext_video', () => {
  it('inlines the thumbnail by default, does not fetch video bytes without save_dir', async () => {
    client.post.mockResolvedValue({ thumbnail: 'thumb64', fps: 24, duration: 1.5, still: false, info: 'i' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_video', { prompt: 'a cat walking' });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/video', { body: { prompt: 'a cat walking' } });
    expect(imageBlocks(res)).toEqual([{ type: 'image', data: 'thumb64', mimeType: 'image/png' }]);
    const body = resultJson(res);
    expect(body.fps).toBe(24);
    expect(body.duration).toBe(1.5);
  });

  it('forces send_video:true and saves video+thumbnail when save_dir is given, omitting inline preview by default', async () => {
    client.post.mockResolvedValue({ video: 'vid64', thumbnail: 'thumb64', fps: 24 });
    saveImages.mockReturnValueOnce(['/out/sdnext_video_x.mp4']).mockReturnValueOnce(['/out/sdnext_thumbnail_x.png']);

    const res = await callTool(server, 'sdnext_video', { prompt: 'cat', save_dir: '/out' });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/video', { body: { prompt: 'cat', send_video: true } });
    expect(saveImages).toHaveBeenNthCalledWith(1, ['vid64'], { dir: '/out', prefix: 'sdnext_video', fmt: 'mp4' });
    expect(saveImages).toHaveBeenNthCalledWith(2, ['thumb64'], { dir: '/out', prefix: 'sdnext_thumbnail', fmt: 'png' });
    const body = resultJson(res);
    expect(body.saved_video).toEqual(['/out/sdnext_video_x.mp4']);
    expect(body.saved_thumbnail).toEqual(['/out/sdnext_thumbnail_x.png']);
    expect(imageBlocks(res)).toEqual([]);
  });

  it('return_images:true forces the thumbnail inline even when save_dir is given', async () => {
    client.post.mockResolvedValue({ video: 'vid64', thumbnail: 'thumb64' });
    saveImages.mockReturnValue(['/out/x']);

    const res = await callTool(server, 'sdnext_video', { prompt: 'cat', save_dir: '/out', return_images: true });

    expect(imageBlocks(res)).toEqual([{ type: 'image', data: 'thumb64', mimeType: 'image/png' }]);
  });

  it('notes that video bytes were generated but not saved when no save_dir is given', async () => {
    client.post.mockResolvedValue({ video: 'vid64', thumbnail: 'thumb64' });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_video', { prompt: 'cat', send_video: true });

    expect(resultJson(res).note).toMatch(/not saved/);
  });

  it('still mode: inlines frames after the thumbnail, capped by SDNEXT_MAX_IMAGES', async () => {
    config.maxImagesInResponse = 2;
    client.post.mockResolvedValue({ thumbnail: 'thumb64', frames: ['f1', 'f2', 'f3'], still: true });
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_video', { prompt: 'cat', frames: 1 });

    expect(imageBlocks(res)).toEqual([
      { type: 'image', data: 'thumb64', mimeType: 'image/png' },
      { type: 'image', data: 'f1', mimeType: 'image/png' },
    ]);
    expect(resultJson(res).preview_note).toMatch(/Showing 2 of 4/);
  });

  it('respects mp4_ext for the saved video file extension and strips it, save_dir, return_images from the outgoing body', async () => {
    client.post.mockResolvedValue({ video: 'vid64' });
    saveImages.mockReturnValue(['/out/x.webm']);

    await callTool(server, 'sdnext_video', { prompt: 'cat', save_dir: '/out', mp4_ext: 'webm', return_images: false });

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/video', { body: { prompt: 'cat', mp4_ext: 'webm', send_video: true } });
    expect(saveImages).toHaveBeenCalledWith(['vid64'], { dir: '/out', prefix: 'sdnext_video', fmt: 'webm' });
  });

  it('handles a nullish API response', async () => {
    client.post.mockResolvedValue(null);
    saveImages.mockReturnValue([]);

    const res = await callTool(server, 'sdnext_video', { prompt: 'cat' });

    expect(imageBlocks(res)).toEqual([]);
    expect(resultJson(res)).toEqual({});
  });
});

describe('sdnext_prompt_enhance', () => {
  it('passes the cleaned args straight through to the API (no image shaping)', async () => {
    client.post.mockResolvedValue({ prompt: 'enhanced' });

    const res = await callTool(server, 'sdnext_prompt_enhance', { prompt: 'cat', model: undefined });
    const body = resultJson(res);

    expect(client.post).toHaveBeenCalledWith('/sdapi/v1/prompt-enhance', { body: { prompt: 'cat' } });
    expect(body.prompt).toBe('enhanced');
  });
});
