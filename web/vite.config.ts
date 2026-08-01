import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import { createDemoDatasetReadyCheck, fullStackReadyPlugin } from './build/fullStackReadyPlugin.ts'
import { sourceLocationPlugin } from './build/sourceLocationPlugin.ts'

export default defineConfig({
  plugins: [
    sourceLocationPlugin(),
    fullStackReadyPlugin({
      healthUrl: 'http://127.0.0.1:8080/api/v1/health',
      pageUrl: 'http://127.0.0.1:5173/',
      checkReady: createDemoDatasetReadyCheck({ apiBaseUrl: 'http://127.0.0.1:8080' }),
    }),
    vue(),
  ],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'build/**/*.test.ts'],
  },
})
