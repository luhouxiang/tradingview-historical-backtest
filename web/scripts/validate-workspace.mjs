import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const [layoutPath, drawingPath] = process.argv.slice(2)
if (!layoutPath || !drawingPath) throw new Error('usage: node scripts/validate-workspace.mjs <layout.json> <drawings.json>')
const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const contractRoot = path.resolve(webRoot, '..', 'contracts', 'schemas')
const [layoutSchema, drawingSchema, layout, drawings] = await Promise.all([
  fs.readFile(path.join(contractRoot, 'layout.schema.json'), 'utf8').then(JSON.parse),
  fs.readFile(path.join(contractRoot, 'drawings.schema.json'), 'utf8').then(JSON.parse),
  fs.readFile(path.resolve(layoutPath), 'utf8').then(JSON.parse),
  fs.readFile(path.resolve(drawingPath), 'utf8').then(JSON.parse),
])
const ajv = new Ajv2020({ allErrors: true, strict: false })
addFormats(ajv)
for (const [schema, value, name] of [[layoutSchema, layout, 'layout'], [drawingSchema, drawings, 'drawings']]) {
  const validate = ajv.compile(schema)
  if (!validate(value)) throw new Error(`${name} validation failed: ${ajv.errorsText(validate.errors)}`)
}
console.log(`Validated workspace revisions: ${layout.revision}/${drawings.revision}`)
