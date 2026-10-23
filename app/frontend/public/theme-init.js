(function () {
  var STORAGE_KEY = 'cfb-theme'
  var theme = null
  try {
    var stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') theme = stored
  } catch {
    theme = null
  }
  if (theme === null) {
    try {
      theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    } catch {
      theme = 'light'
    }
  }
  document.documentElement.setAttribute('data-theme', theme)
})()
