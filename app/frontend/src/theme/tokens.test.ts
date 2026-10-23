import { readFileSync } from 'node:fs'

import { resolveFrom } from '../test/paths.ts'
import { describe, expect, it } from 'vitest'

import { contrastRatio } from './contrast.ts'
import { renderThemeCss } from './themeCss.ts'
import {
  BASE_FONT_SIZE_PX,
  cssVariables,
  DARK,
  LIGHT,
  MARK_CONTRAST_MIN,
  MARK_PAIRS,
  RELIEF_PAIRS,
  TEXT_CONTRAST_MIN,
  TEXT_PAIRS,
  THEMES,
  type ThemeName,
} from './tokens.ts'

const themeNames = Object.keys(THEMES) as ThemeName[]

describe.each(themeNames)('%s theme contrast floors', (name) => {
  const tokens = THEMES[name]

  it.each(TEXT_PAIRS.map(([label, pick, ground]) => [label, pick, ground] as const))(
    'text %s reaches 4.5:1',
    (_label, pick, ground) => {
      expect(contrastRatio(pick(tokens), tokens[ground])).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
    },
  )

  it.each(MARK_PAIRS.map(([label, pick]) => [label, pick] as const))(
    'control or chart color %s reaches 3:1 on the page and the surface',
    (_label, pick) => {
      expect(contrastRatio(pick(tokens), tokens.page)).toBeGreaterThanOrEqual(MARK_CONTRAST_MIN)
      expect(contrastRatio(pick(tokens), tokens.surface)).toBeGreaterThanOrEqual(MARK_CONTRAST_MIN)
    },
  )

  it('keeps button text readable on the accent', () => {
    expect(contrastRatio(tokens.onAccent, tokens.accent)).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
  })

  it('uses a sequential ramp that darkens (light to dark) in one hue', () => {
    const lightness = tokens.seq.map((hex) => contrastRatio(hex, '#ffffff'))
    expect([...lightness].sort((a, b) => a - b)).toEqual(lightness)
  })
})

describe('relief colors', () => {
  it('are the status colors that ship with an icon and a label', () => {
    const names = RELIEF_PAIRS.map(([label]) => label)
    expect(names).toEqual(['status.warning', 'status.serious'])
    expect(contrastRatio(LIGHT.status.warning, LIGHT.surface)).toBeLessThan(MARK_CONTRAST_MIN)
  })
})

describe('base size', () => {
  it('is at least 18 px in the generated css', () => {
    expect(BASE_FONT_SIZE_PX).toBeGreaterThanOrEqual(18)
    const base = readFileSync(resolveFrom(import.meta.url, './base.css'), 'utf8')
    const size = /html\s*{[^}]*font-size:\s*(\d+)px/.exec(base)
    expect(Number(size?.[1])).toBe(BASE_FONT_SIZE_PX)
  })
})

describe('pinned palette', () => {
  it('keeps the validated light values', () => {
    expect({
      page: LIGHT.page,
      surface: LIGHT.surface,
      live: LIGHT.series.live,
      replay: LIGHT.series.replay,
      seqBar: LIGHT.seqBar,
      up: LIGHT.movement.up,
      down: LIGHT.movement.down,
      status: LIGHT.status,
    }).toEqual({
      page: '#f9f9f7',
      surface: '#fcfcfb',
      live: '#2a78d6',
      replay: '#eb6834',
      seqBar: '#2a78d6',
      up: '#2a78d6',
      down: '#e34948',
      status: { good: '#0ca30c', warning: '#fab219', serious: '#ec835a', critical: '#d03b3b' },
    })
  })

  it('keeps the validated dark values', () => {
    expect({
      page: DARK.page,
      surface: DARK.surface,
      live: DARK.series.live,
      replay: DARK.series.replay,
      seqBar: DARK.seqBar,
      up: DARK.movement.up,
      down: DARK.movement.down,
      status: DARK.status,
    }).toEqual({
      page: '#0d0d0d',
      surface: '#1a1a19',
      live: '#3987e5',
      replay: '#d95926',
      seqBar: '#3987e5',
      up: '#3987e5',
      down: '#e66767',
      status: { good: '#0ca30c', warning: '#fab219', serious: '#ec835a', critical: '#d03b3b' },
    })
  })

  it('keeps the sequential ramp the dataviz palette defines', () => {
    expect(LIGHT.seq).toEqual(['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'])
    expect(DARK.seq).toEqual(LIGHT.seq)
  })
})

describe('theme.css', () => {
  const css = readFileSync(resolveFrom(import.meta.url, './theme.css'), 'utf8')

  it('is the generated output of the tokens', () => {
    expect(css).toBe(renderThemeCss())
  })

  it.each(themeNames)('defines every %s variable', (name) => {
    for (const [variable, value] of Object.entries(cssVariables(THEMES[name]))) {
      expect(css).toContain(`${variable}: ${value};`)
    }
  })

  it('selects dark by system preference unless light is chosen, and by an explicit choice', () => {
    expect(css).toContain('@media (prefers-color-scheme: dark)')
    expect(css).toContain(':root:where(:not([data-theme="light"]))')
    expect(css).toContain(':root[data-theme="dark"]')
  })
})
