import type { ReactNode } from 'react'

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg
      className="icon"
      aria-hidden="true"
      focusable="false"
      width={20}
      height={20}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  )
}

export function PlusIcon() {
  return (
    <Svg>
      <path d="M10 4v12M4 10h12" />
    </Svg>
  )
}

export function MinusIcon() {
  return (
    <Svg>
      <path d="M4 10h12" />
    </Svg>
  )
}

export function FitIcon() {
  return (
    <Svg>
      <path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4" />
    </Svg>
  )
}

export function MapIcon() {
  return (
    <Svg>
      <path d="M3 5.5 7.5 4l5 1.5L17 4v10.5L12.5 16l-5-1.5L3 16zM7.5 4v10.5M12.5 5.5V16" />
    </Svg>
  )
}

export function ListIcon() {
  return (
    <Svg>
      <path d="M7 5h10M7 10h10M7 15h10M3 5h.01M3 10h.01M3 15h.01" />
    </Svg>
  )
}
