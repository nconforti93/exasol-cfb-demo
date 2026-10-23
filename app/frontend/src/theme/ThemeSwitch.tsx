import { useState } from 'react'

export const THEME_STORAGE_KEY = 'cfb-theme'

export type Theme = 'light' | 'dark'

function currentTheme(): Theme {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

function remember(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // The choice then lasts for this page load only.
  }
}

/** Switches the page between the light and the dark theme; the choice is remembered when storage works. */
export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const next: Theme = theme === 'dark' ? 'light' : 'dark'

  function choose() {
    document.documentElement.setAttribute('data-theme', next)
    remember(next)
    setTheme(next)
  }

  return (
    <button type="button" className="shell-button" onClick={choose} aria-label={`Switch to ${next} theme`}>
      {next === 'dark' ? 'Dark' : 'Light'}
    </button>
  )
}
