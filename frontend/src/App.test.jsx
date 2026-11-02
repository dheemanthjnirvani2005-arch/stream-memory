import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('dashboard shell', () => {
  it('loads empty first-run overview from the API', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url).includes('/api/twitch/status')) return { ok: true, json: async () => ({ connected: false, listener: 'disconnected' }) }
      return { ok: true, json: async () => [] }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText('Your community’s story starts here.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /capture recent chat/i })).toBeDisabled()
    expect(fetchMock).toHaveBeenCalledWith('/api/twitch/status', expect.objectContaining({ credentials: 'include' }))
  })

  it('navigates to inbox and settings', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/api/twitch/status')) return { ok: true, json: async () => ({ connected: false }) }
      return { ok: true, json: async () => [] }
    }))
    render(<App />)
    const navigation = within(screen.getByRole('navigation', { name: 'Desktop sidebar navigation' }))
    fireEvent.click(navigation.getByRole('button', { name: /memory inbox/i }))
    expect(await screen.findByRole('heading', { name: 'Memory inbox' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument()
  })

  it('shows an actionable message when EventSub authentication fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/api/twitch/status')) return { ok: true, json: async () => ({
        connected: true,
        listener: 'authentication_error',
        listener_error: 'Twitch rejected EventSub authentication. Reconnect Twitch to restore live chat.',
      }) }
      return { ok: true, json: async () => [] }
    }))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Reconnect Twitch to restore live chat.')
    expect(screen.getByRole('link', { name: 'Reconnect Twitch' })).toHaveAttribute('href', '/auth/twitch/login')
  })
})
