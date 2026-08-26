// tools/generation.js — 生成类工具：txt2img / img2img / control / process / detail / prompt enhance
import { z } from 'zod';
import { client, clean } from '../client.js';
import { config } from '../config.js';
import { saveImages } from '../image.js';
import { registerTool, imageContent } from '../registry.js';
import { coreGen, hiresGen, refinerGen, detailerGen, loraGen, img2imgGen, controlGen, xyzGen } from './_common.js';

const returnImagesField = {
  return_images: z.boolean().optional().describe(
    'Include generated image bytes in the tool response, as MCP image content (default: true unless save_dir is set — inlining is a large token cost when the file is already on disk).'
  ),
};

function withSave() {
  return {
    ...coreGen,
    ...hiresGen,
    ...refinerGen,
    ...detailerGen,
    ...loraGen,
    save_dir: z.string().optional().describe('Local directory to write returned images into (creates it).'),
    ...returnImagesField,
  };
}

// Whether to inline image bytes in the response: explicit return_images wins,
// otherwise default to inlining only when there's no save_dir to point to instead.
function resolveReturnImages(args) {
  return typeof args.return_images === 'boolean' ? args.return_images : !args.save_dir;
}

// Shapes a text summary + capped MCP image content blocks from a list of base64
// images, saving all of them to save_dir (if given) regardless of the cap.
function shapeImageResult(images, args, extra = {}) {
  const list = Array.isArray(images) ? images : [];
  const written = saveImages(list, { dir: args.save_dir });
  const wantInline = resolveReturnImages(args);
  const cap = config.maxImagesInResponse;
  const embedded = wantInline ? list.slice(0, cap) : [];
  const hiddenByCap = list.length - embedded.length;

  const summary = {
    image_count: list.length,
    ...(written.length ? { saved_files: written } : {}),
    ...(wantInline && hiddenByCap > 0
      ? { note: `Showing ${embedded.length} of ${list.length} image(s) inline (SDNEXT_MAX_IMAGES=${cap}).${written.length ? '' : ' Set save_dir to keep the rest.'}` }
      : {}),
    ...(!wantInline && list.length
      ? { note: `${list.length} image(s) generated and saved to disk, not included inline. Pass return_images:true to include them (large token cost).` }
      : {}),
    ...extra,
  };

  return {
    __mcpContent: [
      { type: 'text', text: JSON.stringify(summary, null, 2) },
      ...imageContent(embedded),
    ],
  };
}

export function register(server) {
  // ── text2img ────────────────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_txt2img',
    title: 'Text to Image',
    method: 'POST',
    path: '/sdapi/v1/txt2img',
    description: 'Generate images from text prompts (SD.Next /sdapi/v1/txt2img). Supports samplers, modular guidance, hires fix, refiner, detailer, LoRA (prompt <lora:name:weight>), styles, scripts, control units, face modules, XYZ grid. Returns a JSON summary (saved_files, info, parameters) plus generated images as MCP image content (capped at SDNEXT_MAX_IMAGES, inlined only when save_dir is unset unless return_images overrides that).',
    inputSchema: { ...withSave(), ...controlGen, ...xyzGen },
    handler: async (args) => {
      const needsBytes = !!args.save_dir || resolveReturnImages(args);
      const { return_images, ...rest } = args;
      const body = clean({ ...rest, send_images: needsBytes ? true : rest.send_images });
      const data = await client.post('/sdapi/v1/txt2img', { body });
      return shapeImageResult(data?.images, args, { info: data?.info, parameters: data?.parameters });
    },
  });

  // ── img2img ─────────────────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_img2img',
    title: 'Image to Image',
    method: 'POST',
    path: '/sdapi/v1/img2img',
    description: 'Generate images from input images with denoising (SD.Next /sdapi/v1/img2img). Supports inpainting via mask (white = repaint), color correction, hires fix, control units, and all txt2img parameters. init_images are base64 (or "upload:<id>" refs).',
    inputSchema: { ...withSave(), ...img2imgGen, ...controlGen, ...xyzGen },
    handler: async (args) => {
      const needsBytes = !!args.save_dir || resolveReturnImages(args);
      const { return_images, ...rest } = args;
      const body = clean({ ...rest, send_images: needsBytes ? true : rest.send_images });
      const data = await client.post('/sdapi/v1/img2img', { body });
      return shapeImageResult(data?.images, args, { info: data?.info, parameters: data?.parameters });
    },
  });

  // ── control (dedicated control-guided endpoint) ────────────────────────
  registerTool(server, {
    name: 'sdnext_control',
    title: 'Control-guided Generation',
    method: 'POST',
    path: '/sdapi/v1/control',
    description: 'Run the control-guided pipeline (SD.Next /sdapi/v1/control) with one or more control units (controlnet / t2i adapter / xs / lite / reference / ip). Returns a JSON summary plus generated images as MCP image content (see sdnext_txt2img for the save_dir/return_images/SDNEXT_MAX_IMAGES behavior).',
    inputSchema: { ...coreGen, ...hiresGen, control_units: z.array(z.any()).describe('Control units (see sdnext_txt2img control_units).'), unit_type: z.string().optional().describe('Request-level default unit type.'), face: z.record(z.any()).optional(), ip_adapter: z.array(z.any()).optional(), xyz: z.record(z.any()).optional(), save_dir: z.string().optional(), ...returnImagesField },
    handler: async (args) => {
      const needsBytes = !!args.save_dir || resolveReturnImages(args);
      const { return_images, ...rest } = args;
      const body = clean({ ...rest, send_images: needsBytes ? true : rest.send_images });
      const data = await client.post('/sdapi/v1/control', { body });
      return shapeImageResult(data?.images, args, { info: data?.info, parameters: data?.parameters });
    },
  });

  // ── extras: process single image (upscaler / HiresFix) ─────────────────
  registerTool(server, {
    name: 'sdnext_process_image',
    title: 'Upscale / Process Image',
    method: 'POST',
    path: '/sdapi/v1/process',
    description: 'Process a single image with the Extras pipeline (upscalers, e.g. from sdnext_list_upscalers). SD.Next /sdapi/v1/process. resize_mode 0 = factor, 1 = target w/h.',
    inputSchema: {
      image: z.string().describe('Base64 input image (or "upload:<id>").'),
      resize_mode: z.number().optional().describe('0 = upscale by factor, 1 = target width/height.'),
      upscaling_resize: z.number().min(1).max(8).optional().describe('Upscale factor (resize_mode=0).'),
      upscaling_resize_w: z.number().int().optional().describe('Target width (resize_mode=1).'),
      upscaling_resize_h: z.number().int().optional().describe('Target height (resize_mode=1).'),
      upscaling_crop: z.boolean().optional().describe('Crop to fit target size.'),
      upscaler_1: z.string().optional().describe('Main upscaler name.'),
      upscaler_2: z.string().optional().describe('Refine upscaler name.'),
      extras_upscaler_2_visibility: z.number().min(0).max(1).optional().describe('Secondary upscaler visibility.'),
      show_extras_results: z.boolean().optional().describe('Return the processed image.'),
      script_args: z.record(z.any()).optional().describe('Per-script args keyed by script name.'),
      save_dir: z.string().optional(),
      ...returnImagesField,
    },
    handler: async (args) => {
      const needsBytes = !!args.save_dir || resolveReturnImages(args);
      const { return_images, ...rest } = args;
      const body = clean({ ...rest, show_extras_results: needsBytes ? true : rest.show_extras_results });
      const data = await client.post('/sdapi/v1/process', { body });
      const { image, ...dataRest } = data ?? {};
      return shapeImageResult(image ? [image] : [], args, dataRest);
    },
  });

  // ── process batch ───────────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_process_batch',
    title: 'Upscale / Process Batch',
    method: 'POST',
    path: '/sdapi/v1/process-batch',
    description: 'Process multiple images at once (SD.Next /sdapi/v1/process-batch). Same options as sdnext_process_image plus an images array.',
    inputSchema: {
      images: z.array(z.string()).describe('Base64 input images.'),
      resize_mode: z.number().optional(), upscaling_resize: z.number().optional(), upscaling_resize_w: z.number().int().optional(),
      upscaling_resize_h: z.number().int().optional(), upscaling_crop: z.boolean().optional(),
      upscaler_1: z.string().optional(), upscaler_2: z.string().optional(), extras_upscaler_2_visibility: z.number().optional(),
      show_extras_results: z.boolean().optional(), script_args: z.record(z.any()).optional(), save_dir: z.string().optional(),
      ...returnImagesField,
    },
    handler: async (args) => {
      const needsBytes = !!args.save_dir || resolveReturnImages(args);
      const { return_images, ...rest } = args;
      const body = clean({ ...rest, show_extras_results: needsBytes ? true : rest.show_extras_results });
      const data = await client.post('/sdapi/v1/process-batch', { body });
      const { images, ...dataRest } = data ?? {};
      return shapeImageResult(images, args, dataRest);
    },
  });

  // ── detailer standalone ─────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_detail',
    title: 'Detailer Pass',
    method: 'POST',
    path: '/sdapi/v1/detail',
    description: 'Run the YOLO detailer on a single image as a standalone operation (SD.Next /sdapi/v1/detail) — no base generation pass. All detailer_* fields fall back to global settings when omitted.',
    inputSchema: {
      image: z.string().describe('Base64 input image.'),
      seed: z.number().int().optional(),
      detailer_models: z.array(z.string()).optional().describe('Detailer model names (from sdnext_list_detailers).'),
      detailer_prompt: z.string().optional(), detailer_negative: z.string().optional(),
      detailer_steps: z.number().int().optional(), detailer_strength: z.number().optional(),
      detailer_resolution: z.number().int().optional(), detailer_sampler: z.string().optional(),
      detailer_prediction: z.string().optional(), detailer_shift: z.number().optional(),
      detailer_cfg_scale: z.number().optional(), detailer_loworder: z.boolean().optional(),
      detailer_thresholding: z.boolean().optional(), detailer_dynamic: z.boolean().optional(),
      detailer_rescale: z.boolean().optional(), detailer_classes: z.string().optional(),
      detailer_conf: z.number().optional(), detailer_iou: z.number().optional(),
      detailer_max: z.number().int().optional(), detailer_min_size: z.number().optional(),
      detailer_max_size: z.number().optional(), detailer_blur: z.number().int().optional(),
      detailer_padding: z.number().int().optional(), detailer_segmentation: z.boolean().optional(),
      detailer_merge: z.boolean().optional(), detailer_sort: z.boolean().optional(),
      detailer_sigma_adjust: z.number().optional(), detailer_sigma_adjust_max: z.number().optional(),
      detailer_include_detections: z.boolean().optional(), save_dir: z.string().optional(),
      ...returnImagesField,
    },
    handler: async (args) => {
      const { return_images, ...rest } = args;
      const data = await client.post('/sdapi/v1/detail', { body: clean(rest) });
      const { image, detections, ...dataRest } = data ?? {};
      return shapeImageResult([image, detections].filter(Boolean), args, dataRest);
    },
  });

  // ── video generation ────────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_video',
    title: 'Generate Video',
    method: 'POST',
    path: '/sdapi/v1/video',
    description: 'Generate a video, or a single still frame, using a video model (SD.Next /sdapi/v1/video). Omit engine and model to drive the currently loaded checkpoint when it is video-capable; pass both (from sdnext_list_video_models) to load a registry model on demand. frames:1 produces still image(s) in the response `frames` array instead of a video. The generated video itself is never returned inline (no MCP content type for video, and files are far too large) — pass save_dir to write it to disk; the thumbnail (and any still frames) are shown inline like other generation tools, subject to return_images/SDNEXT_MAX_IMAGES.',
    inputSchema: {
      engine: z.string().optional().describe('Video engine family; omit together with model to use the currently loaded checkpoint.'),
      model: z.string().optional().describe('Video model name within the engine; see sdnext_list_video_models.'),
      prompt: z.string().describe('Text prompt.'),
      negative_prompt: z.string().optional().describe('Negative text prompt.'),
      styles: z.array(z.string()).optional().describe('Prompt style names to apply.'),
      width: z.number().int().optional().describe('Output width; snapped to the model canvas multiple (default 832).'),
      height: z.number().int().optional().describe('Output height; snapped to the model canvas multiple (default 480).'),
      frames: z.number().int().optional().describe('Number of frames; 1 produces a still image on workflow models (default 17).'),
      steps: z.number().int().optional().describe('Number of inference steps (default 50).'),
      sampler_name: z.string().optional().describe('Sampler name; "Default" keeps the model scheduler.'),
      sampler_shift: z.number().optional().describe('Scheduler flow shift; -1 keeps the model default.'),
      dynamic_shift: z.boolean().optional().describe('Enable dynamic scheduler shifting.'),
      seed: z.number().int().optional().describe('Generation seed; -1 for random.'),
      guidance_scale: z.number().optional().describe('CFG scale; -1 keeps the model default.'),
      guidance_true: z.number().optional().describe('True CFG scale; -1 keeps the model default.'),
      init_image: z.string().optional().describe('Base64 or data URI for the first-frame image (image-to-video).'),
      init_strength: z.number().optional().describe('Denoising strength for the init image (default 0.8).'),
      last_image: z.string().optional().describe('Base64 or data URI for the last-frame image.'),
      references: z.array(z.string()).optional().describe('Reference images for a reference workflow, in model read order.'),
      vae_type: z.string().optional().describe('Decode variant: Default, Tiny, Remote, or Upscale.'),
      vae_tile_frames: z.number().int().optional().describe('Frames per VAE decode tile (default 16).'),
      audio: z.boolean().optional().describe('Generate audio on models that support it (default true).'),
      mp4_fps: z.number().int().optional().describe('Frames per second of the saved video (default 24).'),
      mp4_interpolate: z.number().int().optional().describe('RIFE interpolation passes between frames.'),
      mp4_codec: z.string().optional().describe('Video codec; "none" skips video encoding (default "libx264").'),
      mp4_ext: z.string().optional().describe('Container extension; also used as the local save file extension (default "mp4").'),
      mp4_opt: z.string().optional().describe('Encoder options as key=value pairs separated by : or , (default "crf=16").'),
      mp4_video: z.boolean().optional().describe('Write the video container to disk on the SD.Next server itself (default true) — separate from this tool\'s own save_dir.'),
      mp4_frames: z.boolean().optional().describe('Write individual frame images to disk on the SD.Next server itself.'),
      mp4_sf: z.boolean().optional().describe('Write raw frames as a safetensors file on the SD.Next server itself.'),
      mp4_thumb: z.boolean().optional().describe('Write a thumbnail image next to the video on the SD.Next server itself (default true).'),
      override_settings: z.record(z.any()).optional().describe('Setting overrides applied for this generation only.'),
      script_args: z.array(z.any()).optional().describe('Positional arguments for a selectable script.'),
      alwayson_scripts: z.record(z.array(z.any())).optional().describe('Always-on script args keyed by script name.'),
      extra: z.record(z.any()).optional().describe('Extra attributes set on the processing object.'),
      save_dir: z.string().optional().describe('Local directory to write the generated video (and thumbnail) into (creates it).'),
      ...returnImagesField,
    },
    handler: async (args) => {
      const needsVideoBytes = !!args.save_dir;
      const { return_images, save_dir, mp4_ext, ...rest } = args;
      const body = clean({ ...rest, mp4_ext, send_video: needsVideoBytes ? true : rest.send_video });
      const data = await client.post('/sdapi/v1/video', { body });
      const { video, thumbnail, frames, ...meta } = data ?? {};

      const videoSaved = video ? saveImages([video], { dir: save_dir, prefix: 'sdnext_video', fmt: mp4_ext || 'mp4' }) : [];
      const thumbSaved = thumbnail ? saveImages([thumbnail], { dir: save_dir, prefix: 'sdnext_thumbnail', fmt: 'png' }) : [];

      const wantInline = resolveReturnImages(args);
      const cap = config.maxImagesInResponse;
      const previewSource = [thumbnail, ...(Array.isArray(frames) ? frames : [])].filter(Boolean);
      const embedded = wantInline ? previewSource.slice(0, cap) : [];

      const summary = {
        ...meta,
        ...(videoSaved.length ? { saved_video: videoSaved } : {}),
        ...(thumbSaved.length ? { saved_thumbnail: thumbSaved } : {}),
        ...(video && !save_dir
          ? { note: 'Video bytes were generated but not saved — pass save_dir to write the video to disk (too large to return inline; there is no MCP video content type).' }
          : {}),
        ...(wantInline && previewSource.length > embedded.length
          ? { preview_note: `Showing ${embedded.length} of ${previewSource.length} preview image(s) inline (SDNEXT_MAX_IMAGES=${cap}).` }
          : {}),
      };

      return {
        __mcpContent: [
          { type: 'text', text: JSON.stringify(summary, null, 2) },
          ...imageContent(embedded),
        ],
      };
    },
  });

  // ── prompt enhance (LLM) ────────────────────────────────────────────────
  registerTool(server, {
    name: 'sdnext_prompt_enhance',
    title: 'Enhance Prompt',
    method: 'POST',
    path: '/sdapi/v1/prompt-enhance',
    description: 'Enhance/expand a prompt with an LLM (SD.Next /sdapi/v1/prompt-enhance). Type: text, image, or video.',
    inputSchema: {
      prompt: z.string().describe('Prompt to enhance.'),
      type: z.enum(['text', 'image', 'video']).default('text'),
      model: z.string().optional().describe('Enhancement model name.'),
      system_prompt: z.string().optional(),
      image: z.string().optional().describe('Base64 image (for type=image).'),
      seed: z.number().int().optional().describe('Seed for prompt generation (-1 = random).'),
      nsfw: z.boolean().optional().describe('Allow NSFW content (default true).'),
      prefix: z.string().optional(), suffix: z.string().optional(),
      do_sample: z.boolean().optional(), min_tokens: z.number().int().optional(), max_tokens: z.number().int().optional(),
      temperature: z.number().optional(), repetition_penalty: z.number().optional(),
      top_k: z.number().int().optional(), top_p: z.number().optional(),
      thinking: z.boolean().optional(), keep_thinking: z.boolean().optional(),
      use_vision: z.boolean().optional(), prefill: z.string().optional(), keep_prefill: z.boolean().optional(),
      custom_args: z.string().optional(), process_words: z.string().optional(),
      semantic_threshold: z.number().optional(), embedding_similarity: z.number().optional(),
      use_openai: z.boolean().optional(),
    },
    handler: async (args) => client.post('/sdapi/v1/prompt-enhance', { body: clean({ ...args }) }),
  });
}
