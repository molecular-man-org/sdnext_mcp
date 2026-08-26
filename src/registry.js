// sdnext_mcp/src/registry.js — 工具注册表（单一事实来源）
// 每个工具文件向这里登记元数据；sdnext_api_list / sdnext_call_api 从这里读取。
// 见 framework/02-mcp-design/tool-catalog.md

import { z } from 'zod';

const tools = new Map();

export function registerTool(server, def) {
  const { name, description, title, inputSchema, annotations, handler } = def;
  server.registerTool(name, {
    title,
    description,
    inputSchema,
    ...(annotations ? { annotations } : {}),
  }, async (args, extra) => {
    try {
      const result = await handler(args, extra);
      return ok(result);
    } catch (e) {
      return fail(e);
    }
  });
  tools.set(name, {
    name,
    description,
    method: def.method || 'POST',
    path: def.path,
    destructive: !!def.destructive,
  });
}

// A handler may return { __mcpContent: [...] } to supply pre-built MCP content
// blocks (e.g. mixing a text summary with image blocks) instead of a plain
// JSON-serializable value — mirrors the __dryRun marker convention in client.js.
export function ok(data) {
  if (data && typeof data === 'object' && Array.isArray(data.__mcpContent)) {
    return { content: data.__mcpContent };
  }
  return { content: [{ type: 'text', text: typeof data === 'string' ? data : JSON.stringify(data, null, 2) }] };
}

// Converts base64 PNG strings into MCP image content blocks, dropping invalid entries.
export function imageContent(images = []) {
  return images
    .filter((b64) => typeof b64 === 'string' && b64.length)
    .map((b64) => ({ type: 'image', data: b64, mimeType: 'image/png' }));
}

export function fail(e) {
  const text = `${e.name || 'Error'}: ${e.message}${e.status ? ` (HTTP ${e.status})` : ''}`;
  return { isError: true, content: [{ type: 'text', text }] };
}

export function listTools() {
  return [...tools.values()].sort((a, b) => a.name.localeCompare(b.name));
}

// 通用 passthrough 工具 schema
export const callApiSchema = z.object({
  method: z.enum(['GET', 'POST', 'PUT']).default('GET').describe('HTTP method'),
  path: z.string().describe('API path, e.g. "/sdapi/v1/samplers" or relative "samplers"'),
  body: z.record(z.any()).optional().describe('JSON request body (POST/PUT)'),
  query: z.record(z.union([z.string(), z.number(), z.boolean()])).optional().describe('Query parameters'),
});
