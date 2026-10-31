import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { buildInfoPlugin } from './scripts/build-info.mjs';

export default defineConfig({
  plugins: [buildInfoPlugin(fileURLToPath(new URL('.', import.meta.url)))],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8011', changeOrigin: false } },
  },
  preview: {
    proxy: { '/api': { target: 'http://127.0.0.1:8011', changeOrigin: false } },
  },
});
