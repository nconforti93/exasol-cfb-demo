import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** A file path relative to the calling module (`import.meta.url`), safe under the jsdom environment's own `URL`. */
export function resolveFrom(metaUrl: string, relative: string): string {
  return resolve(dirname(fileURLToPath(metaUrl)), relative)
}
