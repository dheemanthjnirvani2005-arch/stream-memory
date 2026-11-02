import { useEffect, useState } from 'react'
import { ShieldCheck, Unplug } from 'lucide-react'
import { deleteMemory, disconnectTwitch, getMemories, getTwitchStatus } from '../services/api'

export default function SettingsPage({ status, onChanged }) {
  const [current, setCurrent] = useState(status)
  const [counts, setCounts] = useState({ approved: 0, pending: 0 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    getTwitchStatus().then(setCurrent).catch((requestError) => setError(requestError.message))
    Promise.all([getMemories('approved'), getMemories('pending')]).then(([approved, pending]) => setCounts({ approved: approved.length, pending: pending.length }))
      .catch((requestError) => setError(requestError.message))
  }, [status])
  async function disconnect() {
    if (!window.confirm('Disconnect Twitch? This removes the saved Twitch connection and stops chat listening.')) return
    setBusy(true); setError(''); setNotice('')
    try { await disconnectTwitch(); setCurrent({ connected: false, broadcaster: null, listener: 'disconnected' }); setNotice('Twitch disconnected.'); onChanged() }
    catch (requestError) { setError(requestError.message) }
    finally { setBusy(false) }
  }
  async function deleteAll() {
    if (!window.confirm('Permanently delete all community memories and their supporting evidence? This cannot be undone.')) return
    setBusy(true); setError(''); setNotice('')
    try {
      const all = await getMemories()
      for (const item of all) await deleteMemory(item.id)
      setCounts({ approved: 0, pending: 0 }); setNotice('All community memories were deleted.'); onChanged()
    } catch (requestError) { setError(requestError.message) }
    finally { setBusy(false) }
  }
  return <div className="content page-content"><div className="page-heading"><div><div className="eyebrow">WORKSPACE PREFERENCES</div><h1>Settings</h1><p>Manage your Twitch connection and community memory data.</p></div></div>
    {error && <div className="inline-error" role="alert">{error}</div>}{notice && <div className="success-banner" role="status">{notice}</div>}
    <section className="settings-card"><div className="settings-icon"><Unplug size={20}/></div><div className="settings-copy"><span className="settings-label">TWITCH ACCOUNT</span><h2>{current?.broadcaster || 'No channel connected'}</h2><p>{current?.connected ? `Connection ${current.listener || 'active'}. Chat is linked to this broadcaster.` : 'Connect a Twitch account to capture chat and answer lore questions.'}</p></div><div className="settings-action">{current?.connected ? <button className="button danger" disabled={busy} onClick={disconnect}>Disconnect Twitch</button> : <a className="button primary" href="/auth/twitch/login">Connect Twitch</a>}</div></section>
    <section className="settings-card"><div className="settings-icon"><ShieldCheck size={20}/></div><div className="settings-copy"><span className="settings-label">COMMUNITY DATA</span><h2>Memory management</h2><p>{counts.approved} approved memories · {counts.pending} pending suggestions</p><p className="subtle">Deleting a memory also removes its linked source excerpts.</p></div><div className="settings-action"><button className="button danger" disabled={busy || (!counts.approved && !counts.pending)} onClick={deleteAll}>Delete all memories</button></div></section>
  </div>
}
