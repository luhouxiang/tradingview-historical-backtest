import path from 'node:path'
import type { Plugin } from 'vite'

const LOGGER_CALL = /\blogger\.(trace|debug|info|warn|error)\s*\(/g

function sourceFunction(prefix: string): string {
  const declarations = [...prefix.matchAll(
    /(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)/gm,
  )]
  const latest = declarations.at(-1)
  return latest?.[1] ?? latest?.[2] ?? 'module'
}

export function sourceLocationPlugin(root = process.cwd()): Plugin {
  return {
    name: 'tvbt-source-location',
    enforce: 'pre',
    transform(code, rawId) {
      if (rawId.includes('?')) return null
      const id = rawId.split('?', 1)[0]
      if (!/\.(?:ts|vue)$/.test(id) || id.endsWith('/logging/logger.ts') || id.includes('.test.')) {
        return null
      }
      let changed = false
      const transformed = code.replace(LOGGER_CALL, (call, _level: string, offset: number) => {
        changed = true
        const prefix = code.slice(0, offset)
        const line = prefix.split('\n').length
        const source = path.relative(root, id).replaceAll(path.sep, '/')
        const location = JSON.stringify({
          source_file: source,
          source_line: line,
          source_function: sourceFunction(prefix),
        })
        return `${call}${location}, `
      })
      return changed ? { code: transformed, map: null } : null
    },
  }
}
