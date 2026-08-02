import { readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const __dirname = dirname(fileURLToPath(import.meta.url));
let appVersion = '0.0.0';
let appleTouchIconDataUri = '';
try {
  appVersion = readFileSync(resolve(__dirname, '../VERSION'), 'utf-8').trim();
} catch {}
try {
  const icon = readFileSync(resolve(__dirname, 'public/apple-touch-icon.png'));
  appleTouchIconDataUri = `data:image/png;base64,${icon.toString('base64')}`;
} catch {}

const basePath = (process.env.FLYMAIL_BASE_PATH || '/').replace(/\/+$/, '') || '/';

export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'inline-apple-touch-icon',
      transformIndexHtml(html) {
        return html.replace('__FLYMAIL_APPLE_TOUCH_ICON__', appleTouchIconDataUri || './apple-touch-icon.png');
      },
    },
  ],
  base: basePath.endsWith('/') ? basePath : `${basePath}/`,
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion),
  },
  build: {
    outDir: '../dist/ui',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/vue') || id.includes('/node_modules/@vue/')) return 'vue-core';
          if (id.includes('/node_modules/axios/')) return 'http-core';
          if (id.includes('/node_modules/prosemirror-') || id.includes('/node_modules/@tiptap/pm/')) return 'editor-runtime';
          if (id.includes('/node_modules/@tiptap/core/') || id.includes('/node_modules/@tiptap/vue-3/')) return 'editor-core';
          if (id.includes('/node_modules/@tiptap/')) return 'editor-extensions';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
