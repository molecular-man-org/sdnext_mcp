// tools/models.js — 模型/选项管理（checkpoint、VAE、LoRA 刷新与加载、options）
import { z } from 'zod';
import { client } from '../client.js';
import { registerTool } from '../registry.js';

let srv;
function post(name, title, description, path, schema) {
  registerTool(srv, {
    name, title, description, method: 'POST', path,
    inputSchema: schema ?? {},
    handler: (args) => client.post(path, { body: Object.keys(schema ?? {}).length ? args : undefined }),
  });
}

export function register(server) {
  srv = server;
  post('sdnext_refresh_checkpoints', 'Refresh Checkpoints',
    'Rescan checkpoint model folders (POST /sdapi/v1/refresh-checkpoints).', '/sdapi/v1/refresh-checkpoints');
  post('sdnext_refresh_vae', 'Refresh VAEs',
    'Rescan VAE model folders (POST /sdapi/v1/refresh-vae).', '/sdapi/v1/refresh-vae');
  post('sdnext_refresh_unets', 'Refresh UNets',
    'Rescan UNet/DiT model folders (POST /sdapi/v1/refresh-unets).', '/sdapi/v1/refresh-unets');
  post('sdnext_refresh_loras', 'Refresh LoRAs',
    'Rescan LoRA model folders (POST /sdapi/v1/refresh-loras).', '/sdapi/v1/refresh-loras');
  post('sdnext_unload_checkpoint', 'Unload Checkpoint',
    'Unload the currently loaded checkpoint to free VRAM (POST /sdapi/v1/unload-checkpoint).', '/sdapi/v1/unload-checkpoint');
  post('sdnext_reload_checkpoint', 'Reload Checkpoint',
    'Reload the current checkpoint into VRAM (POST /sdapi/v1/reload-checkpoint).', '/sdapi/v1/reload-checkpoint',
    z.object({ force: z.boolean().optional() }));
  post('sdnext_lock_checkpoint', 'Lock Checkpoint',
    'Prevent the current checkpoint from being unloaded (POST /sdapi/v1/lock-checkpoint).', '/sdapi/v1/lock-checkpoint');

  registerTool(server, {
    name: 'sdnext_select_checkpoint',
    title: 'Select Checkpoint',
    method: 'POST',
    path: '/sdapi/v1/checkpoint',
    description: 'Select which checkpoint model is active (POST /sdapi/v1/checkpoint). Give the checkpoint name or filename.',
    inputSchema: z.object({
      checkpoint: z.string().describe('Checkpoint name or filename (from sdnext_list_checkpoints).'),
    }),
    handler: (args) => client.post('/sdapi/v1/checkpoint', { body: args }),
  });

  registerTool(server, {
    name: 'sdnext_get_checkpoint',
    title: 'Get Active Checkpoint',
    method: 'GET',
    path: '/sdapi/v1/checkpoint',
    description: 'Get info about the currently loaded checkpoint (GET /sdapi/v1/checkpoint).',
    inputSchema: {},
    handler: () => client.get('/sdapi/v1/checkpoint'),
  });

  registerTool(server, {
    name: 'sdnext_latent_history',
    title: 'Select Latent History Entry',
    method: 'POST',
    path: '/sdapi/v1/latents',
    description: 'Select a latent history entry by name (POST /sdapi/v1/latents) for "Vary (Z)"-style latent reuse. List entries via sdnext_list_latents.',
    inputSchema: z.object({ name: z.string().describe('Latent history entry name.') }),
    handler: (args) => client.post('/sdapi/v1/latents', { body: args }),
  });

  registerTool(server, {
    name: 'sdnext_list_latents',
    title: 'List Latent History',
    method: 'GET',
    path: '/sdapi/v1/latents',
    description: 'List available latent history entry names (GET /sdapi/v1/latents).',
    inputSchema: {},
    handler: () => client.get('/sdapi/v1/latents'),
  });

  registerTool(server, {
    name: 'sdnext_get_options',
    title: 'Get Options',
    method: 'GET',
    path: '/sdapi/v1/options',
    description: 'Get all current UI/server options (GET /sdapi/v1/options).',
    inputSchema: {},
    handler: () => client.get('/sdapi/v1/options'),
  });

  registerTool(server, {
    name: 'sdnext_set_options',
    title: 'Set Options',
    method: 'POST',
    path: '/sdapi/v1/options',
    description: 'Set UI/server options at runtime (POST /sdapi/v1/options). Body: {"options": {name: value, ...}}. Discover valid keys with sdnext_get_options / sdnext_options_info.',
    inputSchema: z.object({
      options: z.record(z.any()).describe('Option name → value map.'),
    }),
    handler: (args) => client.post('/sdapi/v1/options', { body: args }),
  });

  registerTool(server, {
    name: 'sdnext_options_info',
    title: 'Options Info',
    method: 'GET',
    path: '/sdapi/v1/options-info',
    description: 'Get metadata for every option: label, section, type, default, component (GET /sdapi/v1/options-info).',
    inputSchema: {},
    handler: () => client.get('/sdapi/v1/options-info'),
  });
}
