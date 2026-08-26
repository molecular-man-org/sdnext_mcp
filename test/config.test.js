import { describe, it, expect } from 'vitest';
import { config, authHeader, assertConfig, ToolConfig } from '../src/config.js';

describe('authHeader()', () => {
  it('prefers Bearer auth when apiKey is set, even if username/password are also set', () => {
    config.apiKey = 'tok123';
    config.username = 'u';
    config.password = 'p';
    expect(authHeader()).toEqual({ Authorization: 'Bearer tok123' });
  });

  it('falls back to Basic auth when only username/password are set', () => {
    config.apiKey = '';
    config.username = 'user';
    config.password = 'pass';
    expect(authHeader()).toEqual({
      Authorization: `Basic ${Buffer.from('user:pass').toString('base64')}`,
    });
  });

  it('returns an empty object when nothing is configured', () => {
    config.apiKey = '';
    config.username = '';
    config.password = '';
    expect(authHeader()).toEqual({});
  });
});

describe('assertConfig()', () => {
  it('passes for the default config', () => {
    config.baseUrl = 'http://127.0.0.1:7860';
    config.timeoutMs = 300000;
    expect(() => assertConfig()).not.toThrow();
  });

  it('throws when baseUrl is not a valid URL', () => {
    config.baseUrl = 'this is not a url';
    expect(() => assertConfig()).toThrow(/SDNEXT_BASE_URL invalid/);
  });

  it('throws when timeoutMs is too small', () => {
    config.baseUrl = 'http://127.0.0.1:7860';
    config.timeoutMs = 500;
    expect(() => assertConfig()).toThrow(/SDNEXT_TIMEOUT_MS too small/);
  });
});

describe('ToolConfig schema', () => {
  it('validates a well-formed tool config', () => {
    const parsed = ToolConfig.parse({
      name: 'sdnext_status',
      description: 'Get server status',
      path: '/sdapi/v1/status',
      method: 'GET',
    });
    expect(parsed.name).toBe('sdnext_status');
  });

  it('rejects an invalid method', () => {
    expect(() =>
      ToolConfig.parse({ name: 'x', description: 'y', path: '/z', method: 'PATCH' })
    ).toThrow();
  });
});
