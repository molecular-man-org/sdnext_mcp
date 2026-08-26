import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../src/client.js', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, client: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() } };
});

import { client } from '../../src/client.js';
import * as enumerators from '../../src/tools/enumerators.js';
import { fakeServer, callTool, resultJson } from '../harness.js';

let server;

beforeEach(() => {
  vi.clearAllMocks();
  server = fakeServer();
  enumerators.register(server);
});

describe('schema-less GET tools', () => {
  it('sdnext_list_samplers passes query: undefined', async () => {
    client.get.mockResolvedValue([{ name: 'euler_a' }]);
    const res = await callTool(server, 'sdnext_list_samplers', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/samplers', { query: undefined });
    expect(resultJson(res)).toEqual([{ name: 'euler_a' }]);
  });

  it('sdnext_list_checkpoints passes query: undefined', async () => {
    client.get.mockResolvedValue([]);
    await callTool(server, 'sdnext_list_checkpoints', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/sd-models', { query: undefined });
  });
});

describe('GET tools with a schema', () => {
  it('sdnext_list_extra_networks passes a query object when filters are given', async () => {
    client.get.mockResolvedValue([]);
    await callTool(server, 'sdnext_list_extra_networks', { page: 'lora' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/extra-networks', { query: { page: 'lora' } });
  });

  it('sdnext_extra_network_detail passes required args as the query', async () => {
    client.get.mockResolvedValue({ name: 'x' });
    await callTool(server, 'sdnext_extra_network_detail', { page: 'lora', name: 'x' });
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/extra-network-detail', { query: { page: 'lora', name: 'x' } });
  });

  it('sdnext_list_controlnets passes the (empty) args object as the query, since this tool has a schema', async () => {
    // Unlike files.js/server.js's get() helper, this file's ternary is keyed on
    // whether the *schema* has keys, not whether *args* does — so a schema-bearing
    // tool always forwards args verbatim, even when called with {}.
    client.get.mockResolvedValue([]);
    await callTool(server, 'sdnext_list_controlnets', {});
    expect(client.get).toHaveBeenCalledWith('/sdapi/v1/controlnets', { query: {} });
  });
});
