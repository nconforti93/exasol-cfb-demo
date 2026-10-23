import type { MouseEvent, ReactNode } from 'react'

import { ThemeSwitch } from '../theme/ThemeSwitch.tsx'
import { useAppActions, useAppState } from '../state/react.tsx'
import { PAGES, effectivePoint, type PageId } from '../state/store.ts'
import { buildPath } from '../state/url.ts'
import { AsOfPicker } from './AsOfPicker.tsx'
import { LedgerBadge } from './LedgerBadge.tsx'
import { QuotaBadge } from './QuotaBadge.tsx'
import { useShellData } from './ShellData.tsx'

const PAGE_LABELS: Record<PageId, string> = {
  odds: 'Odds',
  predictions: 'Predictions',
  upsets: 'Upsets',
  'head-to-head': 'Head-to-head',
}

function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

/** The app name, page tabs, as-of picker, quota and ledger badges, inspector and theme switches, and the refresh slot. */
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

  return (
    <header className="shell-header">
      <div className="shell-header-row">
        <h1 className="shell-title">CFB playoff odds</h1>
        <nav aria-label="Pages" className="shell-tabs">
          {PAGES.map((page) => (
            <a
              key={page}
              href={buildPath({ ...state, page })}
              aria-current={state.page === page ? 'page' : undefined}
              onClick={(event) => {
                if (!isPlainClick(event)) return
                event.preventDefault()
                actions.setPage(page)
              }}
            >
              {PAGE_LABELS[page]}
            </a>
          ))}
        </nav>
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
      <div className="shell-header-row">
        <AsOfPicker
          points={data.points}
          selection={selection}
          following={state.chosen.mode === 'follow'}
          onSeason={data.selectSeason}
          onChoose={choose}
          onFollow={actions.followCurrent}
        />
        <div className="shell-badges">
          <QuotaBadge
            mode={state.config?.fetch_mode ?? null}
            quota={data.quota}
            failed={data.quotaFailed}
            onCheck={() => data.refreshQuota(true)}
          />
          {state.config !== null && <LedgerBadge config={state.config} />}
        </div>
      </div>
      {refreshPanel !== undefined && <div className="shell-header-row shell-refresh">{refreshPanel}</div>}
    </header>
  )
}
