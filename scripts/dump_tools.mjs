// 输出工具目录（name | title | destructive）供 framework 文档使用
import { buildServer } from '../src/app.js';
const s = buildServer();
const rows = Object.entries(s._registeredTools)
  .map(([name, t]) => ({ name, title: t.title || '', d: t.annotations?.destructiveHint ? 'D' : '' }))
  .sort((a, b) => a.name.localeCompare(b.name));
for (const r of rows) console.log(`${r.name}\t${r.title}\t${r.d}`);
console.error('TOTAL', rows.length);
