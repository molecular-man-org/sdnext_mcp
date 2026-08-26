// tools/civitai.js — CivitAI 集成（/sdapi/v2/civitai/* + legacy v1）
import { z } from 'zod';
import { client } from '../client.js';
import { registerTool } from '../registry.js';

const V2 = '/sdapi/v2/civitai';

let srv;
function get(name, title, description, path, schema) {
  registerTool(srv, {
    name, title, description, method: 'GET', path,
    inputSchema: schema ?? {},
    handler: (args) => client.get(path, { query: args && Object.keys(args).length ? args : undefined }),
  });
}
function post(name, title, description, path, schema) {
  registerTool(srv, {
    name, title, description, method: 'POST', path,
    inputSchema: schema ?? {},
    handler: (args) => client.post(path, { body: args }),
  });
}

export function register(server) {
  srv = server;
  get('sdnext_civitai_search', 'CivitAI Search',
    'Search CivitAI models (GET /sdapi/v2/civitai/search). Filters: query, tag, types, sort, period, base_models, nsfw, limit, cursor.',
    `${V2}/search`,
    z.object({
      query: z.string().optional().describe('Search text.'),
      tag: z.string().optional().describe('Tag filter.'),
      types: z.string().optional().describe('Comma-separated model types (Checkpoint, LoRA, Lora, Embedding, ...).'),
      sort: z.string().optional().describe('Sort order (e.g. "Highest Rated", "Most Downloaded", "Newest").'),
      period: z.string().optional().describe('Time period filter (e.g. "week", "month", "year", "alltime").'),
      base_models: z.string().optional().describe('Comma-separated base model filter.'),
      nsfw: z.boolean().optional().describe('Include NSFW results.'),
      limit: z.number().int().optional().default(20),
      cursor: z.string().optional().describe('Pagination cursor from previous page.'),
      username: z.string().optional().describe('Filter by creator username.'),
      favorites: z.boolean().optional().describe('Only show bookmarked models.'),
    }));

  get('sdnext_civitai_model', 'CivitAI Model',
    'Get a single CivitAI model by ID (GET /sdapi/v2/civitai/model/{model_id}).',
    `${V2}/model/{model_id}`,
    z.object({ model_id: z.number().int().describe('CivitAI model ID.') }));

  get('sdnext_civitai_version', 'CivitAI Model Version',
    'Get a single CivitAI model version by ID (GET /sdapi/v2/civitai/version/{version_id}).',
    `${V2}/version/{version_id}`,
    z.object({ version_id: z.number().int().describe('CivitAI version ID.') }));

  get('sdnext_civitai_version_by_hash', 'CivitAI Version by Hash',
    'Resolve a CivitAI version by file hash (GET /sdapi/v2/civitai/version/by-hash/{hash}).',
    `${V2}/version/by-hash/{hash}`,
    z.object({ hash: z.string().describe('SHA256 file hash.') }));

  get('sdnext_civitai_tags', 'CivitAI Tags',
    'Search CivitAI tags (GET /sdapi/v2/civitai/tags).',
    `${V2}/tags`,
    z.object({ query: z.string().optional(), limit: z.number().int().optional().default(20), page: z.number().int().optional().default(1) }));

  get('sdnext_civitai_creators', 'CivitAI Creators',
    'Search CivitAI creators (GET /sdapi/v2/civitai/creators).',
    `${V2}/creators`,
    z.object({ query: z.string().optional(), limit: z.number().int().optional().default(20), page: z.number().int().optional().default(1) }));

  get('sdnext_civitai_images', 'CivitAI Model Images',
    'List images for a CivitAI model or version (GET /sdapi/v2/civitai/images).',
    `${V2}/images`,
    z.object({
      model_id: z.number().int().optional(),
      model_version_id: z.number().int().optional(),
      limit: z.number().int().optional().default(20),
    }));

  get('sdnext_civitai_me', 'CivitAI Me',
    'Get the authenticated CivitAI user (GET /sdapi/v2/civitai/me). Requires a configured token.',
    `${V2}/me`);

  post('sdnext_civitai_download', 'CivitAI Download',
    'Queue a model file download to the models dir (POST /sdapi/v2/civitai/download). url is required; use model_id/version_id from search results to resolve paths.',
    `${V2}/download`,
    z.object({
      url: z.string().describe('Direct file URL (from version file list).'),
      filename: z.string().optional(),
      folder: z.string().optional().describe('Subfolder under models dir (default by model_type).'),
      model_type: z.string().optional().default('Checkpoint').describe('Checkpoint, LoRA, Embedding, ...'),
      expected_hash: z.string().optional(),
      model_name: z.string().optional(), base_model: z.string().optional(), creator: z.string().optional(),
      model_id: z.number().int().optional(), version_id: z.number().int().optional(),
      version_name: z.string().optional(), nsfw: z.boolean().optional(),
      token: z.string().optional().describe('CivitAI API token (falls back to server config).'),
    }));

  post('sdnext_civitai_download_cancel', 'Cancel Download',
    'Cancel a queued CivitAI download (POST /sdapi/v2/civitai/download/{download_id}/cancel).',
    `${V2}/download/{download_id}/cancel`,
    z.object({ download_id: z.string().describe('Download ID from sdnext_civitai_download.') }));

  get('sdnext_civitai_download_status', 'Download Status',
    'List queued/in-progress CivitAI downloads (GET /sdapi/v2/civitai/download/status).',
    `${V2}/download/status`);

  get('sdnext_civitai_settings', 'CivitAI Settings',
    'Get CivitAI integration settings (GET /sdapi/v2/civitai/settings).', `${V2}/settings`);

  post('sdnext_civitai_set_settings', 'Set CivitAI Settings',
    'Update CivitAI integration settings (POST /sdapi/v2/civitai/settings): token, save_subfolder, save_subfolder_enabled, discard_hash_mismatch.',
    `${V2}/settings`,
    z.object({
      token: z.string().optional().describe('CivitAI API token (validated server-side).'),
      save_subfolder: z.string().optional(),
      save_subfolder_enabled: z.boolean().optional(),
      discard_hash_mismatch: z.boolean().optional(),
    }));

  get('sdnext_civitai_resolve_path', 'Resolve Model Path',
    'Resolve the local save path for a CivitAI model/version (GET /sdapi/v2/civitai/resolve-path).',
    `${V2}/resolve-path`,
    z.object({
      model_type: z.string().optional().default('Checkpoint'),
      model_name: z.string().optional(), base_model: z.string().optional(), creator: z.string().optional(),
      model_id: z.number().int().optional(), version_id: z.number().int().optional(),
      version_name: z.string().optional(), nsfw: z.boolean().optional().default(false),
    }));

  post('sdnext_civitai_metadata_scan', 'Metadata Scan',
    'Scan local model files and match CivitAI metadata (POST /sdapi/v2/civitai/metadata/scan).',
    `${V2}/metadata/scan`);

  post('sdnext_civitai_metadata_update', 'Metadata Update',
    'Update sidecar metadata for local models (POST /sdapi/v2/civitai/metadata/update).',
    `${V2}/metadata/update`);

  get('sdnext_civitai_bookmarks', 'List Bookmarks',
    'List bookmarked CivitAI models (GET /sdapi/v2/civitai/bookmarks).', `${V2}/bookmarks`);

  post('sdnext_civitai_add_bookmark', 'Add Bookmark',
    'Bookmark a CivitAI model name (POST /sdapi/v2/civitai/bookmarks).',
    `${V2}/bookmarks`,
    z.object({ name: z.string().describe('Model name to bookmark.') }));

  post('sdnext_civitai_remove_bookmark', 'Remove Bookmark',
    'Remove a CivitAI bookmark (DELETE via POST /sdapi/v2/civitai/bookmarks/{name}).',
    `${V2}/bookmarks/{name}`,
    z.object({ name: z.string().describe('Bookmark name to remove.') }));

  get('sdnext_civitai_banned', 'List Banned Models',
    'List banned CivitAI model names (GET /sdapi/v2/civitai/banned).', `${V2}/banned`);

  post('sdnext_civitai_add_banned', 'Ban Model',
    'Ban a CivitAI model name (POST /sdapi/v2/civitai/banned).',
    `${V2}/banned`,
    z.object({ name: z.string().describe('Model name to ban.') }));

  post('sdnext_civitai_remove_banned', 'Unban Model',
    'Remove a banned model name (DELETE via POST /sdapi/v2/civitai/banned/{name}).',
    `${V2}/banned/{name}`,
    z.object({ name: z.string().describe('Banned name to remove.') }));

  get('sdnext_civitai_history', 'Search History',
    'List CivitAI search history (GET /sdapi/v2/civitai/history).',
    `${V2}/history`,
    z.object({ search_type: z.string().optional().describe('Filter: query, tag, filter.') }));

  post('sdnext_civitai_clear_history', 'Clear History',
    'Clear CivitAI search history (DELETE via POST /sdapi/v2/civitai/history).',
    `${V2}/history`, {});

  post('sdnext_civitai_check_local', 'Check Local by Hashes',
    'Check which of the given file hashes are already present locally (POST /sdapi/v2/civitai/check-local).',
    `${V2}/check-local`,
    z.object({ hashes: z.array(z.string()).describe('SHA256 hashes to check.') }));

  registerTool(srv, {
    name: 'sdnext_civitai_legacy',
    title: 'CivitAI Legacy Search',
    method: 'GET',
    path: '/sdapi/v1/civitai',
    description: 'Legacy CivitAI search endpoint (GET /sdapi/v1/civitai). Use sdnext_civitai_search for the v2 API instead.',
    inputSchema: z.object({
      query: z.string().optional(), tag: z.string().optional(), types: z.string().optional(),
      sort: z.string().optional(), period: z.string().optional(), nsfw: z.boolean().optional(),
      limit: z.number().int().optional().default(0), base: z.string().optional(),
      model_id: z.number().int().optional(), exact: z.boolean().optional().default(true),
    }),
    handler: (args) => client.get('/sdapi/v1/civitai', { query: args }),
  });
}
