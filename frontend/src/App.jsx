import { useCallback, useEffect, useState } from 'react'
import { Bell, ChevronRight } from 'lucide-react'
import Sidebar from './components/Sidebar'
import Dashboard from './pages/Dashboard'
import Inbox from './pages/Inbox'
import Lore from './pages/Lore'
import SettingsPage from './pages/Settings'
import { getMemories, getTwitchStatus } from './services/api'
import './styles.css'
import './dashboard.css'

const pageTitles = { overview: 'Overview', inbox: 'Memory inbox', lore: 'Community lore', settings: 'Settings' }

export default function App() {
  const [page, setPage] = useState('overview')
  const [status, setStatus] = useState(null)
  const [pendingCount, setPendingCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [loadError, setLoadError] = useState('')
  const refresh = useCallback(() => setRefreshKey((value) => value + 1), [])

  useEffect(() => {
    let active = true
    Promise.all([getTwitchStatus(), getMemories('pending')]).then(([twitch, pending]) => {
      if (!active) return
      setStatus(twitch)
      setPendingCount(pending.length)
      setLoadError('')
    }).catch((error) => {
      if (!active) return
      if (error.message.includes('401') || error.message.includes('session')) {
        setStatus({ connected: false, broadcaster: null, listener: 'disconnected' })
        setPendingCount(0)
        setLoadError('')
      } else setLoadError(error.message)
    })
    return () => { active = false }
  }, [refreshKey])

  useEffect(() => {
    let active = true
    const pollStatus = async () => {
      try {
        const next = await getTwitchStatus()
        if (!active) return
        setStatus((current) => current && current.connected === next.connected &&
          current.listener === next.listener && current.listener_error === next.listener_error &&
          current.broadcaster === next.broadcaster ? current : next)
      } catch {
        // Keep the last known listener state while the API is temporarily unavailable.
      }
    }
    const timer = window.setInterval(pollStatus, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const contentProps = { status, onChanged: refresh }
  return <div className="shell"><Sidebar page={page} onNavigate={setPage} pendingCount={pendingCount} channelName={status?.broadcaster} /><main className="main">
    <header className="topbar"><div className="crumb">Workspace <ChevronRight size={15} /> <strong>{pageTitles[page]}</strong></div><div className="top-actions"><span className={`db-status ${status?.connected ? 'online' : ''}`}><i /> Twitch {status?.connected ? status.listener : 'not connected'}</span><button className="icon-button notification-button" aria-label="Notifications" onClick={() => setPage('inbox')}><Bell size={17}/>{pendingCount > 0 && <b />}</button></div></header>
    {loadError && <div className="global-error" role="alert">{loadError}<button onClick={refresh}>Retry</button></div>}
    {status?.listener_error && <div className="global-error" role="alert">{status.listener_error} <a className="button small secondary" href="/auth/twitch/login">Reconnect Twitch</a></div>}
    {page === 'overview' && <Dashboard {...contentProps} />}
    {page === 'inbox' && <Inbox {...contentProps} />}
    {page === 'lore' && <Lore {...contentProps} />}
    {page === 'settings' && <SettingsPage {...contentProps} />}
  </main></div>
}
