import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { renderSchema } from './contractSchema.ts'

const typesPath = fileURLToPath(new URL('../src/api/types.ts', import.meta.url))
const target = fileURLToPath(new URL('../src/test/fixtures/contract.schema.json', import.meta.url))
writeFileSync(target, renderSchema(readFileSync(typesPath, 'utf8')))
console.log(`wrote ${target}`)
