import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import { parse } from 'yaml'
import { createDemoDatasetReadyCheck, fullStackReadyPlugin } from './build/fullStackReadyPlugin.ts'
import { sourceLocationPlugin } from './build/sourceLocationPlugin.ts'

function initialInstrument(configPath: string): string {
  const fallback = 'AOL9'
  try {
    const document = parse(readFileSync(configPath, 'utf8')) as { chart?: { initial_instrument?: unknown } }
    const configured = document.chart?.initial_instrument
    return typeof configured === 'string' && configured.trim() ? configured.trim().toUpperCase() : fallback
  } catch {
    return fallback
  }
}

const appConfigPath = resolve(import.meta.dirname, process.env.TVBT_APP_CONFIG ?? '../config/app.yaml')
const apiBaseUrl = process.env.TVBT_GO_BASE_URL ?? 'http://127.0.0.1:8080'
const webPort = Number(process.env.TVBT_WEB_PORT ?? 5173)
const previewPort = Number(process.env.TVBT_PREVIEW_PORT ?? 4173)
const preferredInitialInstrument = initialInstrument(appConfigPath)

export default defineConfig({
  define: {
    __TVBT_INITIAL_INSTRUMENT__: JSON.stringify(preferredInitialInstrument),
  },
  plugins: [
    sourceLocationPlugin(),
    fullStackReadyPlugin({
      healthUrl: `${apiBaseUrl}/api/v1/health`,
      pageUrl: `http://127.0.0.1:${webPort}/`,
      checkReady: createDemoDatasetReadyCheck({
        apiBaseUrl,
        preferredSymbol: preferredInitialInstrument,
      }),
    }),
    vue(),
  ],
  server: {
    port: webPort,
    strictPort: true,
    proxy: {
      '/api': apiBaseUrl,
    },
  },
  preview: {
    port: previewPort,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'build/**/*.test.ts'],
  },
})
