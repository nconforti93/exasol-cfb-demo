import { readFileSync } from 'node:fs'

import Ajv2020 from 'ajv/dist/2020.js'
import { resolveFrom } from '../paths.ts'
import { describe, expect, it } from 'vitest'

import { renderSchema } from '../../../scripts/contractSchema.ts'
import { fixture, fixtureFiles, fixtureNames, fixtureTypes } from './index.ts'

const typesSource = readFileSync(resolveFrom(import.meta.url, '../../api/types.ts'), 'utf8')
const schemaText = readFileSync(resolveFrom(import.meta.url, './contract.schema.json'), 'utf8')
const schema = JSON.parse(schemaText) as { $defs: Record<string, unknown> }

const ajv = new Ajv2020({ strict: true, allErrors: true })
ajv.addSchema(schema, 'contract')

describe('contract.schema.json', () => {
  it('is generated from src/api/types.ts (run `npm run schema`)', () => {
    expect(schemaText).toBe(renderSchema(typesSource))
  })
})

describe('fixtures', () => {
  it('are all listed in manifest.json, and every listed file exists', () => {
    expect([...fixtureFiles()].sort()).toEqual([...fixtureNames].sort())
  })

  it('name only types of the contract', () => {
    for (const type of Object.values(fixtureTypes)) {
      expect(Object.keys(schema.$defs)).toContain(type)
    }
  })

  it.each(fixtureNames)('%s parses as its contract type', (name) => {
    const type = fixtureTypes[name] as string
    const validate = ajv.getSchema(`contract#/$defs/${type}`)
    expect(validate, `no schema for ${type}`).toBeDefined()
    const ok = validate?.(fixture(name))
    expect(ok, JSON.stringify(validate?.errors)).toBe(true)
  })
})

describe('fixture coverage of the demo states', () => {
  it('has the states the component tests rely on', () => {
    const names = new Set(fixtureNames)
    for (const required of [
      'config.json',
      'config.ledger-off.json',
      'config.chat-enabled.json',
      'points.json',
      'odds.json',
      'odds.whatif-unavailable.json',
      'odds.empty.json',
      'timeline.json',
      'timeline.done.json',
      'timeline.stuck.json',
      'preview-plan.json',
      'preview-plan.cached.json',
      'refresh-plan.live.json',
      'error.live-busy.json',
      'teams-map.json',
      'team.json',
      'team-outlook.json',
      'team-outlook.empty.json',
      'league-week.json',
      'league-stats.json',
    ]) {
      expect(names.has(required), required).toBe(true)
    }
  })

  it('keeps probabilities in the odds fixture between 0 and 1', () => {
    const odds = fixture<{ rows: { p_in_cfp: number }[] }>('odds.json')
    for (const row of odds.rows) {
      expect(row.p_in_cfp).toBeGreaterThanOrEqual(0)
      expect(row.p_in_cfp).toBeLessThanOrEqual(1)
    }
  })
})

describe('map fixture', () => {
  interface MapFixture {
    teams: { team_id: number; conference: string | null; latitude: number | null; longitude: number | null }[]
  }
  const map = fixture<MapFixture>('teams-map.json')
  const located = map.teams.filter((team) => team.latitude !== null && team.longitude !== null)

  it('has a team in Honolulu, one without coordinates, and a twelve-team cluster within 40 km', () => {
    expect(located.some((team) => Math.abs((team.latitude ?? 0) - 21.31) < 0.1 && Math.abs((team.longitude ?? 0) + 157.86) < 0.1)).toBe(true)
    expect(map.teams.length - located.length).toBe(1)
    const cluster = located.filter((team) => Math.abs((team.latitude ?? 0) - 32.78) < 0.3 && Math.abs((team.longitude ?? 0) + 96.8) < 0.3)
    expect(cluster.length).toBeGreaterThanOrEqual(12)
  })

  it('spans the country and lists the conferences of its teams', () => {
    const latitudes = located.map((team) => team.latitude ?? 0)
    const longitudes = located.map((team) => team.longitude ?? 0)
    expect(Math.max(...latitudes) - Math.min(...latitudes)).toBeGreaterThan(20)
    expect(Math.max(...longitudes) - Math.min(...longitudes)).toBeGreaterThan(40)
    const conferences = fixture<{ conferences: string[] }>('teams-map.json').conferences
    expect([...new Set(map.teams.map((team) => team.conference))].sort()).toEqual([...conferences].sort())
  })
})
