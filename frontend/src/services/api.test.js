import { afterEach, describe, expect, it, vi } from 'vitest'
import { approveMemory, createMemory, getMemories } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('frontend API service', () => {
  it('requests pending records with session credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] })
    vi.stubGlobal('fetch', fetchMock)
    await getMemories('pending')
    expect(fetchMock).toHaveBeenCalledWith('/api/memories?status=pending', expect.objectContaining({ credentials: 'include' }))
  })

  it('serializes create and approval requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'memory-1' }) })
    vi.stubGlobal('fetch', fetchMock)
    await createMemory({ title: 'Moment' })
    await approveMemory('memory-1')
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST', body: JSON.stringify({ title: 'Moment' }) }))
    expect(fetchMock.mock.calls[1][0]).toBe('/api/memories/memory-1/approve')
  })

  it('surfaces API errors without leaking response internals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: 'Database unavailable' }) }))
    await expect(getMemories('approved')).rejects.toThrow('Database unavailable')
  })
})
