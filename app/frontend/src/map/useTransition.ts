import { useEffect, useState } from 'react'

export const TRANSITION_MS = 200

interface Run<T> {
  from: T
  to: T
  progress: number
}

function motionAllowed(): boolean {
  if (typeof window.matchMedia !== 'function' || typeof window.requestAnimationFrame !== 'function') return false
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function easeOut(progress: number): number {
  return 1 - (1 - progress) ** 3
}

function shown<T>(run: Run<T>, blend: (from: T, to: T, t: number) => T): T {
  return run.progress >= 1 ? run.to : blend(run.from, run.to, easeOut(run.progress))
}

/**
 * Eases from the value on screen to a new target over 200 ms (none when the user prefers reduced motion).
 * `target` must keep its identity until it really changes; `blend(from, to, t)` mixes two values.
 */
export function useTransition<T>(target: T, blend: (from: T, to: T, t: number) => T): T {
  const [run, setRun] = useState<Run<T>>({ from: target, to: target, progress: 1 })
  if (run.to !== target) setRun({ from: shown(run, blend), to: target, progress: motionAllowed() ? 0 : 1 })

  const goal = run.to
  const running = run.progress < 1
  useEffect(() => {
    if (!running) return
    let frame = 0
    let start: number | null = null
    const tick = (now: number) => {
      start ??= now
      const progress = Math.min(1, (now - start) / TRANSITION_MS)
      setRun((current) => (current.to === goal ? { ...current, progress } : current))
      if (progress < 1) frame = window.requestAnimationFrame(tick)
    }
    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
  }, [goal, running])

  return shown(run, blend)
}
