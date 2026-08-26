import { describe, it, expect, beforeAll, afterAll } from 'vitest';

// Isolated file (Vitest gives each test file its own module registry) so this
// gets a fresh, unconfigured import of config.js — exercises the "env var
// unset, use default" side of every `||` in the config object literal, and
// the bool()-true branch (insecure/dryRun left unset).
const SDNEXT_KEYS = [
  'SDNEXT_BASE_URL', 'SDNEXT_API_PATH', 'SDNEXT_API_KEY', 'SDNEXT_USERNAME', 'SDNEXT_PASSWORD',
  'SDNEXT_TIMEOUT_MS', 'SDNEXT_INSECURE', 'SDNEXT_MCP_DRY_RUN', 'SDNEXT_MAX_IMAGES',
  'SDNEXT_SAVE_DIR', 'SDNEXT_MCP_TRANSPORT', 'SDNEXT_MCP_PORT', 'SDNEXT_MCP_HOST',
];
const saved = {};
let config;

beforeAll(async () => {
  for (const k of SDNEXT_KEYS) {
    saved[k] = process.env[k];
    delete process.env[k];
  }
  ({ config } = await import('../src/config.js'));
});

afterAll(() => {
  for (const k of SDNEXT_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
});

describe('config defaults (no env vars set)', () => {
  it('uses the documented default for every setting', () => {
    expect(config.baseUrl).toBe('http://127.0.0.1:7860');
    expect(config.apiPath).toBe('/sdapi/v1');
    expect(config.apiKey).toBe('');
    expect(config.username).toBe('');
    expect(config.password).toBe('');
    expect(config.timeoutMs).toBe(300000);
    expect(config.insecure).toBe(false);
    expect(config.dryRun).toBe(false);
    expect(config.maxImagesInResponse).toBe(4);
    expect(config.defaultSaveDir).toBe('');
    expect(config.transport).toBe('stdio');
    expect(config.httpPort).toBe(8787);
    expect(config.httpHost).toBe('127.0.0.1');
  });
});
