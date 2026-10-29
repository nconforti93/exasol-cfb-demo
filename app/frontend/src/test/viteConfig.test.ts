import { describe, expect, it } from 'vitest'

import config from '../../vite.config.ts'

describe('vite.config.ts', () => {
  const server = config.server

  it('binds the dev server to loopback on 5173 and refuses another port', () => {
    expect(server?.host).toBe('127.0.0.1')
    expect(server?.port).toBe(5173)
    expect(server?.strictPort).toBe(true)
  })

  it('leaves the dev server host check on', () => {
    expect(server?.allowedHosts).toBeUndefined()
  })

  it('forwards /api to the backend on loopback without rewriting the Host header', () => {
    const proxy = server?.proxy
    expect(Object.keys(proxy ?? {})).toEqual(['/api'])
    const api = proxy?.['/api']
    expect(api).toEqual({ target: 'http://127.0.0.1:8000', changeOrigin: false })
  })

  it('pins the preview server to loopback too', () => {
    expect(config.preview?.host).toBe('127.0.0.1')
    expect(config.preview?.strictPort).toBe(true)
  })

  it('never exposes the dev server to the network', () => {
    expect(JSON.stringify(server)).not.toContain('0.0.0.0')
    expect(server?.host).not.toBe(true)
  })
})
