import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['test/**/*.test.js'],
    exclude: ['node_modules/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.js'],
      exclude: [
        'node_modules/**',
        'test/**',
        'scripts/**',
        'src/index.js', // trivial CLI bootstrap; can't be exercised in-process, see src/app.js
      ],
      thresholds: {
        perFile: true,
        branches: 80,
      },
    },
  },
});
