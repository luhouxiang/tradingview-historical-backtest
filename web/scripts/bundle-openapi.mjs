import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import SwaggerParser from '@apidevtools/swagger-parser'
import { stringify } from 'yaml'

const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const contractRoot = path.resolve(webRoot, '..', 'contracts')
const source = path.join(contractRoot, 'openapi.yaml')
const output = path.join(contractRoot, 'generated', 'openapi.dereferenced.yaml')
const api = await SwaggerParser.dereference(source)
await fs.mkdir(path.dirname(output), { recursive: true })
await fs.writeFile(output, stringify(api), 'utf8')
console.log(`Dereferenced OpenAPI written to ${path.relative(path.dirname(contractRoot), output)}`)

