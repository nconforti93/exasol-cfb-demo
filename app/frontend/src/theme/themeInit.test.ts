import { readFileSync } from 'node:fs'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { resolveFrom } from '../test/paths.ts'

const scriptPath = resolveFrom(import.meta.url, '../../public/theme-init.js')
const source = readFileSync(scriptPath, 'utf8')
const htmlSource = readFileSync(resolveFrom(import.meta.url, '../../index.html'), 'utf8')

function run(): string | null {
  document.documentElement.removeAttribute('data-theme')
  new Function(source)()
  return document.documentElement.getAttribute('data-theme')
}

function stubStorage(value: string | null, throws = false): void {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    if (throws) throw new Error('storage blocked')
    return value
  })
}

function stubSystem(dark: boolean | 'missing' | 'throws'): void {
  if (dark === 'missing') {
    vi.stubGlobal('matchMedia', undefined)
  } else if (dark === 'throws') {
    vi.stubGlobal('matchMedia', () => {
      throw new Error('no media queries')
    })
  } else {
    vi.stubGlobal('matchMedia', () => ({ matches: dark }))
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('theme-init.js', () => {
  it('uses the stored choice', () => {
    stubStorage('dark')
    stubSystem(false)
    expect(run()).toBe('dark')
  })

  it('ignores a stored value that is not a theme', () => {
    stubStorage('sepia')
    stubSystem(true)
    expect(run()).toBe('dark')
  })

  it('follows the system preference without a stored choice', () => {
    stubStorage(null)
    stubSystem(true)
    expect(run()).toBe('dark')
    stubSystem(false)
    expect(run()).toBe('light')
  })

  it('still sets a theme when storage throws', () => {
    stubStorage(null, true)
    stubSystem(true)
    expect(run()).toBe('dark')
  })

  it('falls back to light when neither storage nor media queries work', () => {
    stubStorage(null, true)
    stubSystem('throws')
    expect(run()).toBe('light')
    stubSystem('missing')
    expect(run()).toBe('light')
  })

  it('is CSP-safe: a same-origin file with no inline script in index.html', () => {
    expect(htmlSource).toContain('<script src="/theme-init.js"></script>')
    const inline = /<script(?![^>]*\bsrc=)[^>]*>/i.exec(htmlSource)
    expect(inline).toBeNull()
    expect(source).not.toMatch(/\beval\s*\(/)
  })

  it('loads before the module script, so the theme is set before the first paint', () => {
    expect(htmlSource.indexOf('/theme-init.js')).toBeLessThan(htmlSource.indexOf('/src/main.tsx'))
  })
})
