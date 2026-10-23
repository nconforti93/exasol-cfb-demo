import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { renderThemeCss } from '../src/theme/themeCss.ts'

const target = fileURLToPath(new URL('../src/theme/theme.css', import.meta.url))
writeFileSync(target, renderThemeCss())
console.log(`wrote ${target}`)
