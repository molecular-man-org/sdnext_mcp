import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import http from 'node:http';

vi.mock('@modelcontextprotocol/sdk/server/stdio.js', () => ({
  StdioServerTransport: vi.fn().mockImplementation(function () {
    return { start: vi.fn().mockResolvedValue(undefined) };
  }),
}));

vi.mock('@modelcontextprotocol/sdk/server/streamableHttp.js', () => ({
  StreamableHTTPServerTransport: vi.fn().mockImplementation(function () {
    return {
      start: vi.fn().mockResolvedValue(undefined),
      handleRequest: vi.fn().mockResolvedValue(undefined),
    };
  }),
}));

import { buildServer, runStdio, runHttp, main } from '../src/app.js';
import { listTools } from '../src/registry.js';
import { config } from '../src/config.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';

let consoleErrorSpy;

beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  // Deliberately NOT vi.restoreAllMocks(): the transport mocks above are
  // plain vi.fn()s (not vi.spyOn), so a blanket restore wipes their
  // mockImplementation instead of reverting anything, breaking every test
  // after the first. Only restore what beforeEach actually spied on.
  consoleErrorSpy.mockRestore();
});

describe('buildServer()', () => {
  it('registers every tool module against a real McpServer instance', () => {
    const server = buildServer();
    expect(server).toBeTruthy();
    expect(listTools().length).toBeGreaterThan(100);
  });
});

describe('runStdio()', () => {
  it('connects a StdioServerTransport and logs the startup line', async () => {
    await expect(runStdio()).resolves.toBeUndefined();
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining('stdio transport up'));
  });
});

describe('runHttp()', () => {
  it('starts an HTTP server, routes /mcp (with or without a query string) to the transport, and 404s everything else', async () => {
    const listen = vi.fn((port, host, cb) => cb());
    let capturedHandler;
    vi.spyOn(http, 'createServer').mockImplementation((handler) => {
      capturedHandler = handler;
      return { listen };
    });

    await runHttp();
    expect(listen).toHaveBeenCalled();

    const res404 = { writeHead: vi.fn().mockReturnThis(), end: vi.fn() };
    capturedHandler({ url: '/nope' }, res404);
    expect(res404.writeHead).toHaveBeenCalledWith(404);
    expect(res404.end).toHaveBeenCalledWith('not found');

    const resMcp = { writeHead: vi.fn().mockReturnThis(), end: vi.fn(), headersSent: false };
    await new Promise((resolve) => {
      capturedHandler({ url: '/mcp' }, resMcp);
      setImmediate(resolve);
    });
    expect(resMcp.writeHead).not.toHaveBeenCalled();

    const resMcpQuery = { writeHead: vi.fn().mockReturnThis(), end: vi.fn(), headersSent: false };
    await new Promise((resolve) => {
      capturedHandler({ url: '/mcp?foo=1' }, resMcpQuery);
      setImmediate(resolve);
    });
    expect(resMcpQuery.writeHead).not.toHaveBeenCalled();
  });

  it('responds 500 when the transport rejects and headers were not already sent', async () => {
    const listen = vi.fn((port, host, cb) => cb());
    let capturedHandler;
    vi.spyOn(http, 'createServer').mockImplementation((handler) => {
      capturedHandler = handler;
      return { listen };
    });
    StreamableHTTPServerTransport.mockImplementationOnce(function () {
      return {
        start: vi.fn().mockResolvedValue(undefined),
        handleRequest: vi.fn().mockRejectedValue(new Error('boom')),
      };
    });

    await runHttp();

    const res = { writeHead: vi.fn().mockReturnThis(), end: vi.fn(), headersSent: false };
    await new Promise((resolve) => {
      capturedHandler({ url: '/mcp' }, res);
      setImmediate(resolve);
    });
    expect(res.writeHead).toHaveBeenCalledWith(500);
    expect(res.end).toHaveBeenCalledWith('boom');
  });

  it('falls back to the raw rejection value when it has no .message', async () => {
    const listen = vi.fn((port, host, cb) => cb());
    let capturedHandler;
    vi.spyOn(http, 'createServer').mockImplementation((handler) => {
      capturedHandler = handler;
      return { listen };
    });
    StreamableHTTPServerTransport.mockImplementationOnce(function () {
      return {
        start: vi.fn().mockResolvedValue(undefined),
        handleRequest: vi.fn().mockRejectedValue('stringy failure'),
      };
    });

    await runHttp();

    const res = { writeHead: vi.fn().mockReturnThis(), end: vi.fn(), headersSent: false };
    await new Promise((resolve) => {
      capturedHandler({ url: '/mcp' }, res);
      setImmediate(resolve);
    });
    expect(res.end).toHaveBeenCalledWith('stringy failure');
  });

  it('does not attempt to write a response when headers were already sent', async () => {
    const listen = vi.fn((port, host, cb) => cb());
    let capturedHandler;
    vi.spyOn(http, 'createServer').mockImplementation((handler) => {
      capturedHandler = handler;
      return { listen };
    });
    StreamableHTTPServerTransport.mockImplementationOnce(function () {
      return {
        start: vi.fn().mockResolvedValue(undefined),
        handleRequest: vi.fn().mockRejectedValue(new Error('boom')),
      };
    });

    await runHttp();

    const res = { writeHead: vi.fn().mockReturnThis(), end: vi.fn(), headersSent: true };
    await new Promise((resolve) => {
      capturedHandler({ url: '/mcp' }, res);
      setImmediate(resolve);
    });
    expect(res.writeHead).not.toHaveBeenCalled();
  });
});

describe('main()', () => {
  it('runs the stdio transport when config.transport is "stdio"', async () => {
    config.transport = 'stdio';
    config.baseUrl = 'http://127.0.0.1:7860';
    config.timeoutMs = 300000;
    await expect(main()).resolves.toBeUndefined();
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining('stdio transport up'));
  });

  it('runs the http transport when config.transport is "http"', async () => {
    const listen = vi.fn((port, host, cb) => cb());
    vi.spyOn(http, 'createServer').mockImplementation(() => ({ listen }));

    config.transport = 'http';
    config.baseUrl = 'http://127.0.0.1:7860';
    config.timeoutMs = 300000;
    await expect(main()).resolves.toBeUndefined();
    expect(listen).toHaveBeenCalled();
  });
});
