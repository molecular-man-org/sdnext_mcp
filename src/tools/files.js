// tools/files.js — 文件/存储/Gallery/上传（"Functional"/"Upload"/"Gallery" tag）
import { z } from 'zod';
import { client } from '../client.js';
import { config, authHeader } from '../config.js';
import { registerTool } from '../registry.js';

const img = z.string().describe('Base64-encoded image (raw base64, data: URL, or "upload:<id>" ref).');
const fileRef = z.string().describe('File path relative to SD.Next data dir (or an absolute path inside allowed dirs, e.g. "outputs/Generations/...").');

let srv;
function get(name, title, description, path, schema) {
  registerTool(srv, {
    name, title, description, method: 'GET', path,
    inputSchema: schema ?? {},
    handler: (args) => client.get(path, { query: args && Object.keys(args).length ? args : undefined }),
  });
}
function del(name, title, description, path, schema) {
  registerTool(srv, {
    name, title, description, method: 'DELETE', path,
    inputSchema: schema,
    destructive: true,
    annotations: { readOnlyHint: false, destructiveHint: true },
    handler: (args) => client.del(path, { query: args && Object.keys(args).length ? args : undefined }),
  });
}

export function register(server) {
  srv = server;
  registerTool(server, {
    name: 'sdnext_upload',
    title: 'Upload File',
    method: 'POST',
    path: '/sdapi/v1/upload',
    description: 'Upload a file to the SD.Next server (POST /sdapi/v1/upload, multipart form). Files land in the system temp dir (or relative to datadir when path is given). The server accepts "upload:<id>" refs in image fields afterwards.',
    inputSchema: z.object({
      filename: z.string().describe('Client file name.'),
      content_base64: z.string().describe('File content as base64.'),
      mime: z.string().optional().default('application/octet-stream'),
      overwrite: z.boolean().optional().describe('Overwrite if the file already exists.'),
      path: z.string().optional().describe('Target dir relative to SD.Next datadir (must exist); empty = system temp.'),
    }),
    handler: async (args) => {
      const { filename, content_base64, mime = 'application/octet-stream', overwrite = false, path = '' } = args;
      if (config.dryRun) {
        return {
          __dryRun: true,
          method: 'POST',
          url: `${config.baseUrl}/sdapi/v1/upload`,
          form: { file: `${filename} (${Buffer.from(content_base64, 'base64').length} bytes, ${mime})`, overwrite, path },
          note: 'DRY RUN (SDNEXT_MCP_DRY_RUN=1): request not sent',
        };
      }
      const bytes = Buffer.from(content_base64, 'base64');
      const form = new FormData();
      const blob = new Blob([bytes], { type: mime });
      form.append('file', blob, filename);
      form.append('overwrite', overwrite ? 'true' : '');
      form.append('path', path);
      const url = `${config.baseUrl}/sdapi/v1/upload`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { ...authHeader() },
        body: form,
        signal: AbortSignal.timeout(config.timeoutMs),
      });
      const text = await res.text();
      let data; try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text.slice(0, 4000) }; }
      if (!res.ok) {
        const detail = data?.detail || text.slice(0, 1000);
        throw new Error(`SD.Next upload ${res.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
      }
      return data;
    },
  });

  get('sdnext_file', 'Fetch Server File',
    'Fetch a file from the server (GET /sdapi/v1/file?file=...) as raw content. Must be inside an allowed dir (outputs, temp, models...).',
    '/sdapi/v1/file',
    z.object({ file: fileRef }));

  del('sdnext_delete_image', 'Delete Image',
    'Delete a generated image file (DELETE /sdapi/v1/delete-image?file=...). DESTRUCTIVE.',
    '/sdapi/v1/delete-image',
    z.object({ file: fileRef }));

  del('sdnext_delete_file', 'Delete File',
    'Delete a file (DELETE /sdapi/v1/delete-file?file=...). DESTRUCTIVE.',
    '/sdapi/v1/delete-file',
    z.object({ file: fileRef }));

  registerTool(server, {
    name: 'sdnext_png_info',
    title: 'Read Image Metadata',
    method: 'POST',
    path: '/sdapi/v1/png-info',
    description: 'Extract generation parameters from a PNG image (POST /sdapi/v1/png-info). Returns raw info string, items dict, and parsed parameters (prompt, seed, sampler, ...).',
    inputSchema: z.object({ image: img }),
    handler: (args) => client.post('/sdapi/v1/png-info', { body: args }),
  });

  get('sdnext_browser_folders', 'List Output Folders',
    'List output/gallery folders with labels (GET /sdapi/v1/browser/folders).', '/sdapi/v1/browser/folders');

  get('sdnext_browser_files', 'List Output Files',
    'List files in an output folder as "folder##F##file" lines (GET /sdapi/v1/browser/files?folder=...).',
    '/sdapi/v1/browser/files',
    z.object({ folder: z.string().describe('Folder path (from sdnext_browser_folders).') }));

  get('sdnext_browser_thumb', 'Output Thumbnail',
    'Get a thumbnail for an output file (GET /sdapi/v1/browser/thumb?file=...).',
    '/sdapi/v1/browser/thumb',
    z.object({ file: fileRef, exif: z.boolean().optional() }));

  get('sdnext_network_thumb', 'Network Thumbnail',
    'Get a thumbnail for an extra-network item (GET /sdapi/v1/network/thumb?file=...).',
    '/sdapi/v1/network/thumb',
    z.object({ file: z.string().describe('Network thumbnail path.') }));
}
