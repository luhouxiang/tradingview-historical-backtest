import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import SwaggerParser from '@apidevtools/swagger-parser'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'

const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const contractRoot = path.resolve(webRoot, '..', 'contracts')
const openapiPath = path.join(contractRoot, 'openapi.yaml')
const api = await SwaggerParser.validate(openapiPath)
const dereferenced = await SwaggerParser.dereference(openapiPath)

if (api.openapi !== '3.1.0') throw new Error(`Expected OpenAPI 3.1.0, found ${api.openapi}`)

const ajv = new Ajv2020({ allErrors: true, strict: false })
addFormats(ajv)

async function json(relative) {
  return JSON.parse(await fs.readFile(path.join(contractRoot, relative), 'utf8'))
}

const fileValidators = new Map()

async function validateFile(schemaPath, examplePath) {
  const example = await json(examplePath)
  let validate = fileValidators.get(schemaPath)
  if (!validate) {
    validate = ajv.compile(await json(schemaPath))
    fileValidators.set(schemaPath, validate)
  }
  if (!validate(example)) {
    throw new Error(`${examplePath} failed ${schemaPath}: ${ajv.errorsText(validate.errors)}`)
  }
}

await validateFile('schemas/dataset-meta.schema.json', 'examples/dataset-meta.json')
await validateFile('schemas/layout.schema.json', 'examples/layout.json')
await validateFile('schemas/log-event.schema.json', 'examples/log-event.json')
await validateFile('schemas/run-manifest.schema.json', 'examples/run-manifest.json')
await validateFile('schemas/run-manifest.schema.json', 'examples/ranking-run-manifest.json')
await validateFile('schemas/run-manifest.schema.json', 'examples/risk-run-manifest.json')
await validateFile('schemas/drawings.schema.json', 'examples/drawings.json')
await validateFile(
  'schemas/strategy-source-config.schema.json',
  'examples/strategy-source-config.json',
)
await validateFile(
  'schemas/indicator-cache-manifest.schema.json',
  'examples/indicator-cache-manifest.json',
)
await validateFile(
  'schemas/chan-cache-manifest.schema.json',
  'examples/chan-cache-manifest.json',
)
await validateFile(
  'schemas/replay-cache-manifest.schema.json',
  'examples/replay-cache-manifest.json',
)
await validateFile('schemas/study-manifest.schema.json', 'examples/study-manifest.json')

for (const [examplePath, schemaName] of [
  ['examples/indicator-job.json', 'CalculationRequest'],
  ['examples/chan-job.json', 'CalculationRequest'],
  ['examples/replay-job.json', 'ReplayRequest'],
  ['examples/backtest-job.json', 'BacktestRequest'],
  ['examples/ranking-backtest-job.json', 'BacktestRequest'],
  ['examples/risk-backtest-job.json', 'BacktestRequest'],
  ['examples/study-job.json', 'StudyRequest'],
  ['examples/chan-calculation-results.json', 'CalculationResults'],
]) {
  const example = await json(examplePath)
  const schema = dereferenced.components.schemas[schemaName]
  const validate = ajv.compile(schema)
  if (!validate(example)) {
    throw new Error(`${examplePath} failed OpenAPI ${schemaName}: ${ajv.errorsText(validate.errors)}`)
  }
}

console.log('OpenAPI and 20 contract examples validated successfully.')
