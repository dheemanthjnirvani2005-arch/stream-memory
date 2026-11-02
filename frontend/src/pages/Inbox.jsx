import { useEffect, useState } from 'react'
import MemoryCard from '../components/MemoryCard'
import MemoryForm from '../components/MemoryForm'
import { approveMemory, getMemories, rejectMemory } from '../services/api'

export default function Inbox({ onChanged }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    getMemories('pending').then((rows) => { if (active) { setItems(rows); setError('') } })
      .catch((requestError) => { if (active) setError(requestError.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [reload])
  async function act(memory, action) {
    setBusy(memory.id); setError(''); setNotice('')
    try {
      if (action === 'approve') await approveMemory(memory.id)
      else await rejectMemory(memory.id)
      setNotice(action === 'approve' ? 'Memory approved and added to community lore.' : 'Suggestion rejected.')
      setReload((value) => value + 1); onChanged()
    } catch (requestError) { setError(requestError.message) }
    finally { setBusy('') }
  }
  return <div className="content page-content"><div className="page-heading"><div><div className="eyebrow">MEMORY REVIEW</div><h1>Memory inbox</h1><p>Review the moments AI found in recent chat. Nothing becomes lore without your approval.</p></div><span className="count-badge">{items.length} pending</span></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-link" onClick={() => setReload((v) => v + 1)}>Retry</button></div>}{notice && <div className="success-banner" role="status">{notice}</div>}
    {loading ? <div className="state-card">Loading suggestions…</div> : items.length === 0 ? <div className="state-card large-empty"><div className="state-icon">✦</div><h2>You’re all caught up.</h2><p>New capture suggestions will appear here for your review.</p></div> : <div className="memory-grid">{items.map((memory) => editing?.id === memory.id ? <section className="panel edit-panel" key={memory.id}><h2>Edit suggestion</h2><MemoryForm memoryId={memory.id} initial={memory} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); setReload((v) => v + 1); onChanged() }}/></section> : <div key={memory.id} className={busy === memory.id ? 'is-busy' : ''}><MemoryCard memory={memory} actions={Object.assign(['approve', 'reject', 'edit'], { onApprove: (item) => act(item, 'approve'), onReject: (item) => act(item, 'reject'), onEdit: setEditing })}/></div>)}</div>}
  </div>
}
