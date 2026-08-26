import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fromFile, fromDataUrl, toDataUrl, saveImages } from '../src/image.js';
import { config } from '../src/config.js';

let tmpDir;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sdnext-mcp-test-'));
  config.defaultSaveDir = '';
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe('fromFile()', () => {
  it('reads a file and base64-encodes it', () => {
    const file = path.join(tmpDir, 'in.bin');
    fs.writeFileSync(file, Buffer.from('hello'));
    expect(fromFile(file)).toBe(Buffer.from('hello').toString('base64'));
  });
});

describe('fromDataUrl()', () => {
  it('extracts the base64 payload from a data: URL', () => {
    const b64 = Buffer.from('hi').toString('base64');
    expect(fromDataUrl(`data:image/png;base64,${b64}`)).toBe(b64);
  });

  it('returns the input unchanged when it is not a data: URL', () => {
    expect(fromDataUrl('rawbase64==')).toBe('rawbase64==');
  });
});

describe('toDataUrl()', () => {
  it('wraps raw base64 in a data: URL with the given mime type', () => {
    expect(toDataUrl('AAAA', 'image/jpeg')).toBe('data:image/jpeg;base64,AAAA');
  });

  it('defaults to image/png when no mime is given', () => {
    expect(toDataUrl('AAAA')).toBe('data:image/png;base64,AAAA');
  });

  it('passes an already-prefixed data: URL through unchanged', () => {
    expect(toDataUrl('data:image/png;base64,AAAA')).toBe('data:image/png;base64,AAAA');
  });
});

describe('saveImages()', () => {
  it('returns [] when no dir is given and no default is configured', () => {
    expect(saveImages(['AAAA'], {})).toEqual([]);
  });

  it('returns [] when images is not an array', () => {
    expect(saveImages(undefined, { dir: tmpDir })).toEqual([]);
  });

  it('writes raw-base64 and data:-prefixed images to dir, skipping invalid entries', () => {
    const b64 = Buffer.from('img-bytes').toString('base64');
    const written = saveImages(['', 123, b64, `data:image/png;base64,${b64}`], { dir: tmpDir, prefix: 'p' });
    expect(written).toHaveLength(2);
    for (const f of written) {
      expect(fs.readFileSync(f)).toEqual(Buffer.from('img-bytes'));
    }
  });

  it('falls back to config.defaultSaveDir when no dir option is passed', () => {
    config.defaultSaveDir = tmpDir;
    const b64 = Buffer.from('x').toString('base64');
    const written = saveImages([b64], {});
    expect(written).toHaveLength(1);
    expect(written[0].startsWith(tmpDir)).toBe(true);
  });
});
