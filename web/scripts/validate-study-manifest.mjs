import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const manifestPath = process.argv[2]
if (!manifestPath) throw new Error('usage: node scripts/validate-study-manifest.mjs <study.json>')
const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const schemaPath = path.resolve(webRoot, '..', 'contracts', 'schemas', 'study-manifest.schema.json')
const [schema, manifest] = await Promise.all([
  fs.readFile(schemaPath, 'utf8').then(JSON.parse),
  fs.readFile(path.resolve(manifestPath), 'utf8').then(JSON.parse),
])
const ajv = new Ajv2020({ allErrors: true, strict: false })
addFormats(ajv)
const validate = ajv.compile(schema)
if (!validate(manifest)) throw new Error(`Study manifest validation failed: ${ajv.errorsText(validate.errors)}`)
console.log(`Validated optimization study: ${manifest.study_id}`)
