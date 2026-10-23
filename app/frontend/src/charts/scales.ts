export function linearScale(domain: readonly [number, number], range: readonly [number, number]): (value: number) => number {
  const [d0, d1] = domain
  const [r0, r1] = range
  if (d0 === d1) return () => r0
  return (value) => r0 + ((value - d0) / (d1 - d0)) * (r1 - r0)
}

function niceStep(raw: number): number {
  const exponent = Math.floor(Math.log10(raw))
  const unit = 10 ** exponent
  const base = raw / unit
  if (base <= 1.2) return unit
  if (base <= 2.4) return 2 * unit
  if (base <= 3) return 2.5 * unit
  if (base <= 6) return 5 * unit
  return 10 * unit
}

/** Round tick values from 0 that cover `max`. */
export function niceTicks(max: number, target = 4): number[] {
  if (max <= 0) return [0, 1]
  const step = niceStep(max / target)
  const ticks: number[] = []
  for (let index = 0; index * step < max + step * 1e-9; index++) {
    ticks.push(Number((index * step).toFixed(10)))
  }
  const last = ticks[ticks.length - 1] ?? 0
  if (last < max) ticks.push(Number((last + step).toFixed(10)))
  return ticks
}
