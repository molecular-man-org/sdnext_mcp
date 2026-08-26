// tools/enumerators.js — 枚举/查询类工具（对应 SD.Next "Enumerators" tag）
import { z } from 'zod';
import { client } from '../client.js';
import { registerTool } from '../registry.js';

let srv;
function get(name, title, description, path, schema, meta = {}) {
  registerTool(srv, {
    name, title, description,
    method: 'GET', path,
    inputSchema: schema ?? {},
    handler: (args) => client.get(path, { query: Object.keys(schema ?? {}).length ? args : undefined }),
    ...meta,
  });
}

export function register(server) {
  srv = server;
  get('sdnext_list_samplers', 'List Samplers',
    'List available samplers and their options (GET /sdapi/v1/samplers). Use the returned names for sampler_name.', '/sdapi/v1/samplers');
  get('sdnext_list_schedulers', 'List Schedulers',
    'List available schedulers with class and options (GET /sdapi/v1/schedulers).', '/sdapi/v1/schedulers');
  get('sdnext_list_checkpoints', 'List Checkpoints',
    'List all registered checkpoint models with title, filename, type and hash (GET /sdapi/v1/sd-models). Use model_name or filename for sd_model_checkpoint.', '/sdapi/v1/sd-models');
  get('sdnext_list_unets', 'List UNet/DiT',
    'List available UNet/DiT models (GET /sdapi/v1/unets).', '/sdapi/v1/unets');
  get('sdnext_list_vae', 'List VAEs',
    'List available VAE models (GET /sdapi/v1/sd-vae).', '/sdapi/v1/sd-vae');
  get('sdnext_list_upscalers', 'List Upscalers',
    'List available upscalers with model name, path and scale (GET /sdapi/v1/upscalers).', '/sdapi/v1/upscalers');
  get('sdnext_list_styles', 'List Prompt Styles',
    'List prompt styles (GET /sdapi/v1/prompt-styles). Apply via styles: ["style name"].', '/sdapi/v1/prompt-styles');
  get('sdnext_list_wildcards', 'List Wildcards',
    'List wildcard files available for <wildcard:name> syntax (GET /sdapi/v1/wildcards).', '/sdapi/v1/wildcards');
  get('sdnext_list_embeddings', 'List Embeddings',
    'List textual-inversion embeddings loaded/skipped for the current model (GET /sdapi/v1/embeddings).', '/sdapi/v1/embeddings');
  get('sdnext_list_loras', 'List LoRAs',
    'List all LoRA models (GET /sdapi/v1/loras). Use in prompt as <lora:name:weight>.', '/sdapi/v1/loras');
  get('sdnext_list_loaded_loras', 'List Loaded LoRAs',
    'List currently loaded LoRA names (GET /sdapi/v1/loaded-loras).', '/sdapi/v1/loaded-loras');
  get('sdnext_list_extra_networks', 'List Extra Networks',
    'List extra networks of a page type (lora, checkpoint, embedding, lyco, hypernetwork...) with optional filters (GET /sdapi/v1/extra-networks).',
    '/sdapi/v1/extra-networks',
    z.object({
      page: z.string().optional().describe('Extra network page: lora, checkpoint, embedding, hypernetwork, lyco, locon, apg, ...'),
      name: z.string().optional(), filename: z.string().optional(), title: z.string().optional(),
      fullname: z.string().optional(), hash: z.string().optional(),
    }));
  get('sdnext_extra_network_detail', 'Extra Network Detail',
    'Get detailed metadata for one extra network item (GET /sdapi/v1/extra-network-detail).',
    '/sdapi/v1/extra-network-detail',
    z.object({
      page: z.string().describe('Extra network page (lora, checkpoint, ...).'),
      name: z.string().describe('Item name.'),
    }));
  get('sdnext_extra_network_details', 'Extra Network Details (batch)',
    'Batch-fetch full details for extra network items with pagination (GET /sdapi/v1/extra-network-details).',
    '/sdapi/v1/extra-network-details',
    z.object({
      page: z.string().optional(), name: z.string().optional(), filename: z.string().optional(),
      title: z.string().optional(), fullname: z.string().optional(), hash: z.string().optional(),
      offset: z.number().int().optional().default(0), limit: z.number().int().optional().default(50),
    }));
  get('sdnext_list_ip_adapters', 'List IP Adapters',
    'List available IP adapter models (GET /sdapi/v1/ip-adapters).', '/sdapi/v1/ip-adapters');
  get('sdnext_list_detailers', 'List Detailer Models',
    'List available YOLO detailer models (GET /sdapi/v1/detailers).', '/sdapi/v1/detailers');
  get('sdnext_list_face_restorers', 'List Face Restorers',
    'List available face restoration models (GET /sdapi/v1/face-restorers).', '/sdapi/v1/face-restorers');
  get('sdnext_list_preprocessors', 'List Control Preprocessors',
    'List control preprocessors with groups and configurable params (GET /sdapi/v1/preprocessors).', '/sdapi/v1/preprocessors');
  get('sdnext_list_controlnets', 'List Control Models',
    'List available ControlNet models, optionally filtered by model type (GET /sdapi/v1/controlnets).',
    '/sdapi/v1/controlnets',
    z.object({ model_type: z.string().optional().describe('Filter by model type.') }));
  get('sdnext_list_control_modes', 'List Control Modes',
    'List valid control modes for Union/ProMax models (GET /sdapi/v1/control-modes).', '/sdapi/v1/control-modes');
  get('sdnext_list_scripts', 'List Scripts',
    'List available scripts with arguments (GET /sdapi/v1/scripts). Use name for script_name.', '/sdapi/v1/scripts');
  get('sdnext_script_info', 'Script Info',
    'Get info for a single script (GET /sdapi/v1/script-info).',
    '/sdapi/v1/script-info',
    z.object({ name: z.string().describe('Script name.'), img2img: z.boolean().optional() }));
  get('sdnext_list_extensions', 'List Extensions',
    'List installed extensions (GET /sdapi/v1/extensions).', '/sdapi/v1/extensions');
  get('sdnext_list_xyz_options', 'List XYZ Grid Options',
    'List XYZ grid axis options, optionally filtered by prefix (GET /sdapi/v1/xyz-grid).',
    '/sdapi/v1/xyz-grid',
    z.object({ option: z.string().optional().describe('Filter axis label by prefix/suffix.') }));
  get('sdnext_autocomplete', 'List Autocomplete Files',
    'List local autocomplete/tag list files (GET /sdapi/v1/autocomplete).', '/sdapi/v1/autocomplete');
  get('sdnext_autocomplete_remote', 'List Remote Autocomplete Files',
    'List remote autocomplete files with update status (GET /sdapi/v1/autocomplete/remote).', '/sdapi/v1/autocomplete/remote');
  get('sdnext_list_vqa_models', 'List VLM Models',
    'List vision-language models with prompts and capabilities (GET /sdapi/v1/vqa/models).', '/sdapi/v1/vqa/models');
  get('sdnext_list_vqa_prompts', 'List VLM Prompt Tasks',
    'List task prompts available for a VLM (GET /sdapi/v1/vqa/prompts).',
    '/sdapi/v1/vqa/prompts',
    z.object({ model: z.string().optional().describe('VLM name to filter tasks for.') }));
  get('sdnext_list_openclip', 'List OpenCLIP Models',
    'List available OpenCLIP captioning models (GET /sdapi/v1/openclip).', '/sdapi/v1/openclip');
  get('sdnext_list_tagger_models', 'List Tagger Models',
    'List available tagger models (wd-tagger, deepdanbooru, etc.) (GET /sdapi/v1/tagger/models).', '/sdapi/v1/tagger/models');
  get('sdnext_list_masking', 'List Masking Options',
    'List segmentation mask models, color maps, params and types (GET /sdapi/v1/masking).', '/sdapi/v1/masking');
  get('sdnext_list_video_models', 'List Video Models',
    'List video engines and models, optionally filtered by engine (GET /sdapi/v1/video/models). Use engine+name for sdnext_video; "loaded" marks the currently-loaded model.',
    '/sdapi/v1/video/models',
    z.object({ engine: z.string().optional().describe('Filter by video engine family (e.g. "Hunyuan Video").') }));
}
