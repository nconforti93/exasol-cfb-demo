import manifest from './manifest.json'

const modules = import.meta.glob<unknown>('./*.json', { eager: true, import: 'default' })

/** Fixture file name → the exported type of `src/api/types.ts` (and model of `app/backend/models.py`) it must parse as. */
export const fixtureTypes: Readonly<Record<string, string>> = manifest

export const fixtureNames: readonly string[] = Object.keys(manifest)

/** A deep copy of a fixture, so a test can change it freely. */
export function fixture<T>(name: string): T {
  const data = modules[`./${name}`]
  if (data === undefined) throw new Error(`unknown fixture ${name}`)
  return structuredClone(data) as T
}

export function fixtureFiles(): string[] {
  return Object.keys(modules)
    .map((path) => path.replace('./', ''))
    .filter((name) => name !== 'manifest.json' && name !== 'contract.schema.json')
}
