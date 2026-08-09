import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import { parse } from 'yaml'
import { createDemoDatasetReadyCheck, fullStackReadyPlugin } from './build/fullStackReadyPlugin.ts'
import { sourceLocationPlugin } from './build/sourceLocationPlugin.ts'

function initialInstrument(): string {
  const fallback = 'AOL9'
  try {
    const configPath = resolve(import.meta.dirname, '../config/app.yaml')
    const document = parse(readFileSync(configPath, 'utf8')) as { chart?: { initial_instrument?: unknown } }
    const configured = document.chart?.initial_instrument
    return typeof configured === 'string' && configured.trim() ? configured.trim().toUpperCase() : fallback
  } catch {
    return fallback
  }
}

const preferredInitialInstrument = initialInstrument()

export default defineConfig({
  define: {
    __TVBT_INITIAL_INSTRUMENT__: JSON.stringify(preferredInitialInstrument),
  },
  plugins: [
    sourceLocationPlugin(),
    fullStackReadyPlugin({
      healthUrl: 'http://127.0.0.1:8080/api/v1/health',
      pageUrl: 'http://127.0.0.1:5173/',
      checkReady: createDemoDatasetReadyCheck({
        apiBaseUrl: 'http://127.0.0.1:8080',
        preferredSymbol: preferredInitialInstrument,
      }),
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
