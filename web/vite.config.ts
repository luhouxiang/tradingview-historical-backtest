import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import { sourceLocationPlugin } from './build/sourceLocationPlugin.ts'

export default defineConfig({
  plugins: [sourceLocationPlugin(), vue()],
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
