#!/usr/bin/env node
// sdnext_mcp/src/index.js — CLI bootstrap. Kept intentionally tiny: everything
// testable lives in app.js, since spawning this file's own execution can't be
// exercised from within the test process.
import { main } from './app.js';

await main();
