import { useId, type CSSProperties, type ReactNode } from 'react'

import { SqlButton } from './SqlButton.tsx'

export type CardSpan = 4 | 6 | 8 | 12

export interface CardProps {
  title?: ReactNode
  /** A small uppercase label above the title. */
  eyebrow?: ReactNode
  /** The team accent (hex) for the top stripe of team-scoped cards. */
  accent?: string
  span?: CardSpan
  /** Extra header actions, left of the SQL button. */
  actions?: ReactNode
  /** The query-log entry behind the card: omit for no SQL button, null for a disabled one. */
  sqlEntry?: string | null
  /** A muted meta line, for example "0 calls · 38 ms · CFB_API.TEAM_TRAVEL". */
  footer?: ReactNode
  className?: string
  children?: ReactNode
}

/** A card of the dashboard grid: optional header (eyebrow, title, actions, SQL), a body with a 12 px gap, and a footer. */
export function Card({ title, eyebrow, accent, span, actions, sqlEntry, footer, className, children }: CardProps) {
  const titleId = useId()
  const hasHeader = title !== undefined || eyebrow !== undefined || actions !== undefined || sqlEntry !== undefined
  const classes = ['card', accent === undefined ? '' : 'card-accent', span === undefined ? '' : `span-${span}`, className ?? '']
    .filter((name) => name !== '')
    .join(' ')
  const style = accent === undefined ? undefined : ({ '--card-accent': accent } as CSSProperties)
  const Root = title === undefined ? 'div' : 'section'
  return (
    <Root className={classes} style={style} {...(title === undefined ? {} : { 'aria-labelledby': titleId })}>
      {hasHeader && (
        <header className="card-header">
          <div className="card-heading">
            {eyebrow !== undefined && <p className="card-eyebrow">{eyebrow}</p>}
            {title !== undefined && (
              <h3 id={titleId} className="card-title">
                {title}
              </h3>
            )}
          </div>
          {(actions !== undefined || sqlEntry !== undefined) && (
            <div className="card-actions">
              {actions}
              {sqlEntry !== undefined && <SqlButton entryId={sqlEntry} />}
            </div>
          )}
        </header>
      )}
      <div className="card-body">{children}</div>
      {footer !== undefined && <footer className="card-footer">{footer}</footer>}
    </Root>
  )
}
