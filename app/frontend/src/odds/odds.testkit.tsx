import { FakeBackend, type Handler } from '../test/backend.ts'
import { fixture } from '../test/fixtures/index.ts'
import { renderInApp, type RenderOptions } from '../test/render.tsx'
import { OutageProvider } from '../state/outage.tsx'
import { OddsPage } from './OddsPage.tsx'

/** A backend that answers every route of the odds page from fixtures; `over` replaces routes. */
export function oddsBackend(over: Record<string, Handler> = {}): FakeBackend {
  return new FakeBackend({
    'GET /api/odds': () => fixture('odds.json'),
    'GET /api/whatif/games': () => fixture('whatif-games.json'),
    'POST /api/whatif/odds': () => fixture('whatif-odds.json'),
    'GET /api/guide': () => fixture('guide.json'),
    'POST /api/whatif/guide': () => fixture('guide.conditional.json'),
    ...over,
  })
}

/** The odds page at 2026 week 9 (the fixtures' point), under the outage state it needs. */
export function renderOdds(backend: FakeBackend = oddsBackend(), initial: RenderOptions['initial'] = {}) {
  return renderInApp(
    <OutageProvider>
      <OddsPage />
    </OutageProvider>,
    { backend, initial: { chosen: { mode: 'explicit', season: 2026, week_seq: 9 }, ...initial } },
  )
}
