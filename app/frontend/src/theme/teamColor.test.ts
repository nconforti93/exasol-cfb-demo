import { describe, expect, it } from 'vitest'

import { contrastRatio } from './contrast.ts'
import { badgeLabel, badgeLook, normalizeHex, readableInk, teamAccent, type TeamColors } from './teamColor.ts'
import { DARK, LIGHT, MARK_CONTRAST_MIN, TEXT_CONTRAST_MIN } from './tokens.ts'

function colors(color: string | null, alternate: string | null): TeamColors {
  return { color, alternate_color: alternate }
}

describe('normalizeHex', () => {
  it.each([
    ['#0B3D91', '#0b3d91'],
    ['0b3d91', '#0b3d91'],
    [' #F2A900 ', '#f2a900'],
  ])('accepts %s with or without the hash', (value, expected) => {
    expect(normalizeHex(value)).toBe(expected)
  })

  it.each([null, undefined, '', 'tbd', '#fff', '#12345', '#1234567', '#gggggg'])('treats %s as missing', (value) => {
    expect(normalizeHex(value)).toBeNull()
  })
})

describe('teamAccent', () => {
  it('uses the team color when it reaches 3:1 against the theme surface', () => {
    expect(teamAccent(colors('#bd1a21', '#ffffff'), 'light')).toBe('#bd1a21')
  })

  it('falls back to the alternate color where the team color is too dark for dark, or too pale for light', () => {
    expect(teamAccent(colors('#0b3d91', '#f2a900'), 'dark')).toBe('#f2a900')
    expect(teamAccent(colors('#f5e663', '#1b1b1b'), 'light')).toBe('#1b1b1b')
  })

  it('falls back to the app accent when neither color reaches 3:1, or both are missing', () => {
    expect(teamAccent(colors('#101010', '#0a0a0a'), 'dark')).toBe(DARK.accent)
    expect(teamAccent(colors('tbd', null), 'light')).toBe(LIGHT.accent)
  })

  it('never returns a color below 3:1 against the surface of its theme', () => {
    const samples = ['#0b3d91', '#f5e663', '#bd1a21', '#ffffff', '#000000', '#7a1f3d', '#cfb87c', 'zzz']
    for (const [theme, tokens] of [['light', LIGHT], ['dark', DARK]] as const) {
      for (const color of samples) {
        for (const alternate of samples) {
          const accent = teamAccent(colors(color, alternate), theme)
          expect(contrastRatio(accent, tokens.surface)).toBeGreaterThanOrEqual(MARK_CONTRAST_MIN)
        }
      }
    }
  })
})

describe('readableInk', () => {
  it('reaches 4.5:1 on every background, including mid-tones that suit neither white nor near-black alone', () => {
    for (let level = 0; level <= 255; level += 5) {
      const hex = `#${level.toString(16).padStart(2, '0').repeat(3)}`
      expect(contrastRatio(readableInk(hex), hex)).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
    }
    for (const hex of ['#0b3d91', '#f5e663', '#bd1a21', '#ff8200', '#5e2d79', '#cfb87c', '#00a0a0']) {
      expect(contrastRatio(readableInk(hex), hex)).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
    }
  })
})

describe('badge', () => {
  it('writes the abbreviation, else the first four letters of the school', () => {
    expect(badgeLabel({ abbreviation: 'BRK', school: 'Brookvale' })).toBe('BRK')
    expect(badgeLabel({ abbreviation: null, school: 'Carlton' })).toBe('CARL')
    expect(badgeLabel({ abbreviation: ' ', school: 'Ox' })).toBe('OX')
  })

  it('is a disc in the team color, ringed in the alternate, with readable ink', () => {
    const look = badgeLook(colors('0b3d91', '#f2a900'))
    expect(look.background).toBe('#0b3d91')
    expect(look.ring).toBe('#f2a900')
    expect(contrastRatio(look.ink, look.background)).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
  })

  it('uses neutral colors for a team with none', () => {
    const look = badgeLook(colors(null, null))
    expect(look.background).toMatch(/^#[0-9a-f]{6}$/)
    expect(contrastRatio(look.ink, look.background)).toBeGreaterThanOrEqual(TEXT_CONTRAST_MIN)
  })
})
