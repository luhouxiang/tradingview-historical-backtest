import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const metaPath = process.argv[2]
if (!metaPath) throw new Error('usage: node scripts/validate-dataset-meta.mjs <meta.json>')

const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const schemaPath = path.resolve(webRoot, '..', 'contracts', 'schemas', 'dataset-meta.schema.json')
const [schema, meta] = await Promise.all([
  fs.readFile(schemaPath, 'utf8').then(JSON.parse),
  fs.readFile(path.resolve(metaPath), 'utf8').then(JSON.parse),
])

const ajv = new Ajv2020({ allErrors: true, strict: false })
addFormats(ajv)
const validate = ajv.compile(schema)
if (!validate(meta)) {
  throw new Error(`dataset metadata validation failed: ${ajv.errorsText(validate.errors)}`)
}
console.log(`Validated dataset metadata: ${meta.dataset_id} ${meta.data_revision}`)
