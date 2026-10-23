import type { ReactNode } from 'react'

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg
      className="icon"
      aria-hidden="true"
      focusable="false"
      width={16}
      height={16}
      viewBox="0 0 16 16"
      fill="none"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  )
}

export function WarningIcon() {
  return (
    <Svg>
      <path d="M8 2 14.5 13.5h-13z" />
      <path d="M8 6.5v3.2M8 11.8v.1" />
    </Svg>
  )
}

export function InfoIcon() {
  return (
    <Svg>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.2V11M8 5v.1" />
    </Svg>
  )
}

export function OkIcon() {
  return (
    <Svg>
      <path d="m3 8.5 3.5 3.5L13 4.5" />
    </Svg>
  )
}

export function SyncIcon() {
  return (
    <Svg>
      <path d="M13 8a5 5 0 1 1-1.5-3.5M13 2.5v3h-3" />
    </Svg>
  )
}
