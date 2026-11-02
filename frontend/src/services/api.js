async function request(path, options = {}) {
  const response = await fetch(path, { credentials: 'include', ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try { message = (await response.json()).detail || message } catch {}
    throw new Error(message)
  }
  if (response.status === 204) return null
  return response.json()
}

export const getHealth = () => request('/health')
export const getTwitchStatus = () => request('/api/twitch/status')
export const getMemories = (status, query = '') => {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (query) params.set('q', query)
  return request(`/api/memories?${params}`)
}
export const createMemory = (data) => request('/api/memories', { method: 'POST', body: JSON.stringify(data) })
export const captureRecentChat = () => request('/api/memories/capture', { method: 'POST' })
export const approveMemory = (id) => request(`/api/memories/${encodeURIComponent(id)}/approve`, { method: 'POST' })
export const rejectMemory = (id) => request(`/api/memories/${encodeURIComponent(id)}/reject`, { method: 'POST' })
export const updateMemory = (id, data) => request(`/api/memories/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) })
export const deleteMemory = (id) => request(`/api/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const disconnectTwitch = () => request('/auth/twitch/disconnect', { method: 'POST' })
