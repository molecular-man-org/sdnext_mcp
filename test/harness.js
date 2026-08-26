// test/harness.js — shared MCP test double for exercising tools/*.js register() functions
import { vi } from 'vitest';

// Mimics the subset of McpServer used by src/registry.js's registerTool().
// Captures each registered tool's config + wrapped handler (the wrapper applies
// the registry's ok()/fail() envelope around the tool's own handler).
export function fakeServer() {
  const tools = new Map();
  return {
    registerTool: vi.fn((name, toolConfig, wrappedHandler) => {
      tools.set(name, { config: toolConfig, handler: wrappedHandler });
    }),
    _tools: tools,
  };
}

// Invokes a registered tool's wrapped handler (name from server._tools) and
// returns the MCP-shaped { content, isError? } result.
export function callTool(server, name, args = {}, extra = {}) {
  const entry = server._tools.get(name);
  if (!entry) throw new Error(`tool not registered: ${name}`);
  return entry.handler(args, extra);
}

// Parses the JSON text out of a successful ok() envelope.
export function resultJson(result) {
  return JSON.parse(result.content[0].text);
}
