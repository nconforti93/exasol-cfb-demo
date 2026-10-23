import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App.tsx'
import './charts/charts.css'
import './shell/shell.css'
import './theme/base.css'
import './theme/theme.css'

const root = document.getElementById('root')
if (root === null) throw new Error('index.html has no #root')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
