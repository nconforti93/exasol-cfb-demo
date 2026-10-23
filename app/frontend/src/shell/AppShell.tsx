import type { ComponentType } from 'react'

import { useAppActions, useAppState } from '../state/react.tsx'
import { useOutage } from '../state/outage.tsx'
import { effectivePoint, type PageId } from '../state/store.ts'
import { useUrlSync } from '../state/urlSync.ts'
import { ChatSlot, type ChatPanelProps } from './ChatSlot.tsx'
import { FollowNoticeBar } from './FollowNotice.tsx'
import { Footer } from './Footer.tsx'
import { Header } from './Header.tsx'
import { WarningIcon } from './icons.tsx'
import { OutageBanner } from './OutageBanner.tsx'
import { ShellProviders, useShellData } from './ShellData.tsx'

export interface AppShellProps {
  /** One component per page. A page renders once the point is known and loads its data with `usePageData`. */
  pages: Record<PageId, ComponentType>
  /** Rendered in the header band on every page; it decides its own collapsed and expanded states. */
  refreshPanel: ComponentType
  /** The drawer's content; the shell adds its heading and the close button. */
  inspector: ComponentType
  /** Phase 7's panel; the region renders only when the configuration enables the chat. */
  chatPanel?: ComponentType<ChatPanelProps>
}

function LoadError() {
  const { loadError } = useShellData()
  const outage = useAppState((state) => state.outage)
  if (loadError === null || outage) return null
  return (
    <div className="banner banner-warning" role="alert">
      <WarningIcon />
      <span>Could not load the app&apos;s data: {loadError.message}</span>
    </div>
  )
}

function PageHost({ pages }: { pages: Record<PageId, ComponentType> }) {
  const page = useAppState((state) => state.page)
  const known = useAppState((state) => effectivePoint(state) !== null)
  const outage = useOutage()
  const failed = useAppState((state) => state.outage)
  const Page = pages[page]
  if (failed) return <OutageBanner message={outage.message} onRetry={outage.retry} />
  if (!known) return <p className="shell-loading">Loading the current point…</p>
  return <Page />
}

function InspectorDrawer({ Inspector }: { Inspector: ComponentType }) {
  const open = useAppState((state) => state.inspector.open)
  const actions = useAppActions()
  if (!open) return null
  return (
    <aside className="inspector-drawer" aria-label="Inspector">
      <div className="inspector-head">
        <h2>Inspector</h2>
        <button type="button" className="shell-button" onClick={() => actions.setInspector({ open: false })}>
          Close inspector
        </button>
      </div>
      <Inspector />
    </aside>
  )
}

function ShellLayout({ pages, refreshPanel: RefreshPanel, inspector, chatPanel }: AppShellProps) {
  useUrlSync()
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to the page
      </a>
      {import.meta.env['VITE_CFB_PREVIEW'] === '1' && (
        <div className="banner banner-warning" role="status">
          <strong>PREVIEW · sample data</strong>
          <span>Served from fixtures: nothing here is real, and no action touches a database or CFBD.</span>
        </div>
      )}
      <Header refreshPanel={<RefreshPanel />} />
      <div className="shell-notices">
        <LoadError />
        <FollowNoticeBar />
      </div>
      <div className="shell-body">
        <main id="main" className="shell-main">
          <PageHost pages={pages} />
        </main>
        <InspectorDrawer Inspector={inspector} />
        <ChatSlot {...(chatPanel === undefined ? {} : { panel: chatPanel })} />
      </div>
      <Footer />
    </div>
  )
}

/** The frame around the pages: header, notices, the page, the inspector drawer, the chat region and the footer. */
export function AppShell(props: AppShellProps) {
  return (
    <ShellProviders>
      <ShellLayout {...props} />
    </ShellProviders>
  )
}
