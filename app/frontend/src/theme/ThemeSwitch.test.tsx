import { readFileSync } from 'node:fs'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveFrom } from '../test/paths.ts'
import { THEME_STORAGE_KEY, ThemeSwitch } from './ThemeSwitch.tsx'

const initScript = readFileSync(resolveFrom(import.meta.url, '../../public/theme-init.js'), 'utf8')

function renderSwitch() {
  return render(
    <StrictMode>
      <ThemeSwitch />
    </StrictMode>,
  )
}

beforeEach(() => {
  document.documentElement.setAttribute('data-theme', 'light')
  window.localStorage.clear()
})

afterEach(() => {
  document.documentElement.removeAttribute('data-theme')
})

describe('ThemeSwitch', () => {
  it('offers the other theme than the one the page has', () => {
    renderSwitch()
    expect(screen.getByRole('button', { name: 'Switch to dark theme' })).toBeInTheDocument()
  })

  it('offers light when the page is dark', () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    renderSwitch()
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('changes the page theme and remembers it under the key the theme script reads', async () => {
    renderSwitch()
    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(initScript).toContain(`'${THEME_STORAGE_KEY}'`)
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('switches back to light', async () => {
    renderSwitch()
    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
    await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
  })

  it('still switches when the browser storage throws', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    renderSwitch()
    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('treats a page without a theme as light', () => {
    document.documentElement.removeAttribute('data-theme')
    renderSwitch()
    expect(screen.getByRole('button', { name: 'Switch to dark theme' })).toBeInTheDocument()
  })
})
