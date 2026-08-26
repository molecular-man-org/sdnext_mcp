import { describe, it, expect, beforeAll, afterAll } from 'vitest';

// Isolated file, fresh module registry — exercises the "env var set, use
// override" side of every `||` in the config object literal, plus the
// bool()-false-then-includes() path (SDNEXT_INSECURE='1', case-insensitively
// via SDNEXT_MCP_DRY_RUN='TRUE') and the baseUrl trailing-slash strip.
const overrides = {
  SDNEXT_BASE_URL: 'http://example.com:1234///',
  SDNEXT_API_PATH: '/api/v2',
  SDNEXT_API_KEY: 'secret',
  SDNEXT_USERNAME: 'u',
  SDNEXT_PASSWORD: 'p',
  SDNEXT_TIMEOUT_MS: '5000',
  SDNEXT_INSECURE: '1',
  SDNEXT_MCP_DRY_RUN: 'TRUE',
  SDNEXT_MAX_IMAGES: '9',
  SDNEXT_SAVE_DIR: '/tmp/out',
  SDNEXT_MCP_TRANSPORT: 'http',
  SDNEXT_MCP_PORT: '9999',
  SDNEXT_MCP_HOST: '0.0.0.0',
};
const saved = {};
let config;

beforeAll(async () => {
  for (const [k, v] of Object.entries(overrides)) {
    saved[k] = process.env[k];
    process.env[k] = v;
  }
  ({ config } = await import('../src/config.js'));
});

afterAll(() => {
  for (const k of Object.keys(overrides)) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
});

describe('config overrides (every env var set)', () => {
  it('reads every override, strips trailing slashes from baseUrl, and parses bools case-insensitively', () => {
    expect(config.baseUrl).toBe('http://example.com:1234');
    expect(config.apiPath).toBe('/api/v2');
    expect(config.apiKey).toBe('secret');
    expect(config.username).toBe('u');
    expect(config.password).toBe('p');
    expect(config.timeoutMs).toBe(5000);
    expect(config.insecure).toBe(true);
    expect(config.dryRun).toBe(true);
    expect(config.maxImagesInResponse).toBe(9);
    expect(config.defaultSaveDir).toBe('/tmp/out');
    expect(config.transport).toBe('http');
    expect(config.httpPort).toBe(9999);
    expect(config.httpHost).toBe('0.0.0.0');
  });
});
