import type { MouseEvent, ReactNode } from 'react'

import { ThemeSwitch } from '../theme/ThemeSwitch.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { effectivePoint, isLeagueTab, type Destination, type PageId } from '../state/store.ts'
import { pathFor } from '../state/url.ts'
import { AsOfPicker } from './AsOfPicker.tsx'
import { LedgerBadge } from './LedgerBadge.tsx'
import { QuotaBadge } from './QuotaBadge.tsx'
import { useShellData } from './ShellData.tsx'
import { TeamSearch } from './TeamSearch.tsx'

function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

function isCurrent(section: 'map' | 'league' | 'head-to-head', page: PageId): boolean {
  if (section === 'league') return isLeagueTab(page)
  if (section === 'map') return page === 'home' || page === 'team'
  return page === 'head-to-head'
}

/** The app name, the three sections (Map, League, Head-to-head), team search, as-of picker, badges, switches and the refresh slot. */
export function Header({ refreshPanel }: { refreshPanel?: ReactNode }) {
  const state = useAppState((current) => current)
  const actions = useAppActions()
  const data = useShellData()
  const selection = effectivePoint(state)

  function choose(point: { season: number; week_seq: number }) {
    const picked = data.points?.points.find((candidate) => candidate.as_of_week_seq === point.week_seq)
    if (picked?.is_current === true) actions.followCurrent()
    else actions.choosePoint(point.season, point.week_seq)
  }

  const sections: ReadonlyArray<{ id: 'map' | 'league' | 'head-to-head'; label: string; to: Destination }> = [
    { id: 'map', label: 'Map', to: { page: 'home' } },
    { id: 'league', label: 'League', to: { page: 'week' } },
    { id: 'head-to-head', label: 'Head-to-head', to: { page: 'head-to-head', team: state.pair.team, opponent: state.pair.opponent } },
  ]

  function link(to: Destination, label: ReactNode, key: string, current: boolean, className?: string) {
    return (
      <a
        key={key}
        href={pathFor(state, to)}
        {...(className === undefined ? {} : { className })}
        aria-current={current ? 'page' : undefined}
        onClick={(event) => {
          if (!isPlainClick(event)) return
          event.preventDefault()
          actions.navigate(to)
        }}
      >
        {label}
      </a>
    )
  }

  return (
    <header className="shell-header">
      <div className="shell-header-inner">
        <div className="shell-header-row shell-primary">
          <h1 className="shell-title">{link({ page: 'home' }, 'CFB playoff odds', 'brand', false, 'shell-brand')}</h1>
          <nav aria-label="Pages" className="shell-tabs">
            {sections.map((section) => link(section.to, section.label, section.id, isCurrent(section.id, state.page)))}
          </nav>
          <TeamSearch />
          <div className="shell-badges">
            <QuotaBadge
              mode={state.config?.fetch_mode ?? null}
              quota={data.quota}
              failed={data.quotaFailed}
              onCheck={() => data.refreshQuota(true)}
            />
            {state.config !== null && <LedgerBadge config={state.config} />}
          </div>
          <div className="shell-controls">
            <button
              type="button"
              className="shell-button"
              aria-pressed={state.inspector.open}
              onClick={() => actions.setInspector({ open: !state.inspector.open })}
            >
              Inspector
            </button>
            <ThemeSwitch />
          </div>
        </div>
        <div className="shell-header-row shell-context">
          <AsOfPicker
            points={data.points}
            selection={selection}
            following={state.chosen.mode === 'follow'}
            onSeason={data.selectSeason}
            onChoose={choose}
            onFollow={actions.followCurrent}
          />
          {refreshPanel}
        </div>
      </div>
    </header>
  )
}
