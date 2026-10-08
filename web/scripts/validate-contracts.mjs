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
await validateFile(
  'schemas/strategy-comparison-manifest.schema.json',
  'examples/strategy-comparison-manifest.json',
)
await validateFile(
  'schemas/research-study-manifest.schema.json',
  'examples/research-study-manifest.json',
)
await validateFile(
  'schemas/research-study-manifest.schema.json',
  'examples/research-walk-forward-manifest.json',
)

for (const [examplePath, schemaName] of [
  ['examples/indicator-job.json', 'CalculationRequest'],
  ['examples/chan-job.json', 'CalculationRequest'],
  ['examples/replay-job.json', 'ReplayRequest'],
  ['examples/backtest-job.json', 'BacktestRequest'],
  ['examples/ranking-backtest-job.json', 'BacktestRequest'],
  ['examples/risk-backtest-job.json', 'BacktestRequest'],
  ['examples/study-job.json', 'StudyRequest'],
  ['examples/research-study-job.json', 'ResearchStudyRequest'],
  ['examples/chan-calculation-results.json', 'CalculationResults'],
  ['examples/training-command.json', 'TrainingCommand'],
  ['examples/training-snapshot.json', 'TrainingSnapshot'],
  ['examples/training-create.json', 'TrainingCreateRequest'],
  ['examples/training-accepted.json', 'TrainingAccepted'],
  ['examples/training-status.json', 'TrainingStatus'],
]) {
  const example = await json(examplePath)
  const schema = dereferenced.components.schemas[schemaName]
  const validate = ajv.compile(schema)
  if (!validate(example)) {
    throw new Error(`${examplePath} failed OpenAPI ${schemaName}: ${ajv.errorsText(validate.errors)}`)
  }
}

const trainingCommandValidator = ajv.compile(dereferenced.components.schemas.TrainingCommand)
for (const invalid of [
  {session_id:'session',operation_id:'operation',action:'advance',count:1},
  {session_id:'session',operation_id:'operation',action:'advance',expected_cursor:0,count:101},
  {session_id:'session',operation_id:'operation',action:'seek',expected_cursor:0},
]) {
  if (trainingCommandValidator(invalid)) throw new Error('Invalid training control passed its schema')
}
const trainingStatusValidator = ajv.compile(dereferenced.components.schemas.TrainingStatus)
if (!trainingStatusValidator({request_id:'request',session_id:'training-example',job_id:'job',status:'completed',snapshot:await json('examples/training-snapshot.json')})) {
  throw new Error(`Completed training status failed schema: ${ajv.errorsText(trainingStatusValidator.errors)}`)
}
console.log('OpenAPI and 29 contract examples plus training control/status edge cases validated successfully.')
