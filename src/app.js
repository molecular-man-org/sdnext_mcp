// sdnext_mcp/src/app.js — 构建 McpServer，注册全部工具，选择传输层
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import http from 'node:http';

import { config, assertConfig } from './config.js';

import * as generation from './tools/generation.js';
import * as enumerators from './tools/enumerators.js';
import * as caption from './tools/caption.js';
import * as models from './tools/models.js';
import * as serverTools from './tools/server.js';
import * as files from './tools/files.js';
import * as control from './tools/control.js';
import * as moderation from './tools/moderation.js';
import * as civitai from './tools/civitai.js';
import * as meta from './tools/meta.js';

const MODULES = [generation, enumerators, caption, models, serverTools, files, control, moderation, civitai, meta];

export function buildServer() {
  const server = new McpServer({
    name: 'sdnext-mcp',
    version: '0.1.0',
  }, {
    capabilities: { tools: {} },
    instructions: [
      'MCP server for SD.Next (Stable Diffusion Next) WebUI REST API.',
      'Requires a running SD.Next instance started with --api --listen (base URL from SDNEXT_BASE_URL, default http://127.0.0.1:7860).',
      'Images are raw base64 PNG (no data: prefix) in and out; "upload:<id>" refs are accepted where images are inputs.',
      'Default canvas is 1024x1024 (SD.Next differs from A1111 512).',
      'Use sdnext_list_* tools to discover valid names (samplers, checkpoints, LoRAs, ...) before generating.',
      'Call sdnext_list_tools for the full catalog, or sdnext_call_api for any uncovered endpoint.',
    ].join('\n'),
  });
  for (const m of MODULES) m.register(server);
  return server;
}

export async function runStdio() {
  const server = buildServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`[sdnext-mcp] stdio transport up; target=${config.baseUrl}${config.apiPath} dryRun=${config.dryRun}`);
}

export async function runHttp() {
  const server = buildServer();
  // 无状态模式：每个请求独立处理，便于 MCP 客户端直连
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  await server.connect(transport);

  const httpServer = http.createServer((req, res) => {
    if (req.url !== '/mcp' && !req.url?.startsWith('/mcp?')) {
      res.writeHead(404).end('not found');
      return;
    }
    transport.handleRequest(req, res).catch((e) => {
      console.error('[sdnext-mcp] request error:', e);
      if (!res.headersSent) res.writeHead(500).end(String(e?.message || e));
    });
  });
  httpServer.listen(config.httpPort, config.httpHost, () => {
    console.error(`[sdnext-mcp] streamable-http transport on http://${config.httpHost}:${config.httpPort}/mcp; target=${config.baseUrl}${config.apiPath} dryRun=${config.dryRun}`);
  });
}

export async function main() {
  assertConfig();
  if (config.transport === 'http') await runHttp();
  else await runStdio();
}
