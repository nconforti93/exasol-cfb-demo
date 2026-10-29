import type { ReactNode } from 'react'

export interface PageHeaderProps {
  title: string
  /** One muted line under the title, for example the point's label. */
  subtitle?: ReactNode
  actions?: ReactNode
  id?: string
}

/** The title block of a page: a level-2 heading in the page size, a meta line and the page's actions. */
export function PageHeader({ title, subtitle, actions, id }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <h2 id={id} className="page-title">
          {title}
        </h2>
        {subtitle !== undefined && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions !== undefined && <div className="page-actions">{actions}</div>}
    </header>
  )
}
