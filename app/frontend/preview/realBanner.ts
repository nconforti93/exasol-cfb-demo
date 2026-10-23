import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const TITLE = '<strong>PREVIEW · sample data</strong>'
const DETAIL = '<span>Served from fixtures: nothing here is real, and no action touches a database or CFBD.</span>'

interface Manifest {
  target: string
  season: number
  exported_at: string
}

/** The banner text of a real-data snapshot, from its `manifest.json`; null when the directory has none. */
export function snapshotBanner(directory: string): { title: string; detail: string } | null {
  const path = join(directory, 'manifest.json')
  if (!existsSync(path)) return null
  const manifest = JSON.parse(readFileSync(path, 'utf8')) as Manifest
  return {
    title: `PREVIEW · real data snapshot, ${manifest.exported_at}`,
    detail:
      `Map, teams, this week and stats are real Exasol data (${manifest.target}, season ${manifest.season}, read-only). ` +
      'Odds, predictions, upsets, head-to-head and team outlooks are SAMPLE data from a simulated league.',
  }
}

/** Rewrites the shell's preview banner for a real-data snapshot, so the shell source stays untouched. */
export function realBannerPlugin(directory: string) {
  return {
    name: 'cfb-real-banner',
    enforce: 'pre' as const,
    transform(this: { warn: (message: string) => void }, code: string, id: string) {
      if (!id.endsWith('/shell/AppShell.tsx')) return null
      const banner = snapshotBanner(directory)
      if (banner === null) return null
      if (!code.includes(TITLE) || !code.includes(DETAIL)) {
        this.warn('real-data banner: the shell banner text changed, so the sample-data banner stays')
        return null
      }
      return { code: code.replace(TITLE, `<strong>${banner.title}</strong>`).replace(DETAIL, `<span>${banner.detail}</span>`), map: null }
    },
  }
}
