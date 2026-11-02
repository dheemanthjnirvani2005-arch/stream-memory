import { useEffect, useState } from 'react'
import { ArrowUpRight, BookOpen, Clock3, Plus, Radio, Sparkles, WandSparkles } from 'lucide-react'
import MemoryForm from '../components/MemoryForm'
import { captureRecentChat, getMemories } from '../services/api'

function formatDate(value) {
  if (!value) return 'Date not recorded'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export default function Dashboard({ status, onChanged }) {
  const [approved, setApproved] = useState([])
  const [pending, setPending] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [captureState, setCaptureState] = useState('idle')
  const [manualOpen, setManualOpen] = useState(false)
  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([getMemories('approved'), getMemories('pending')]).then(([approvedRows, pendingRows]) => {
      if (!active) return
      setApproved(approvedRows)
      setPending(pendingRows)
      setError('')
    }).catch((requestError) => { if (active) setError(requestError.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [status, onChanged])
  async function capture() {
    setCaptureState('working')
    try {
      const result = await captureRecentChat()
      setCaptureState(result.created ? 'created' : result.reason === 'empty_chat_buffer' ? 'empty' : 'no-result')
      if (result.created) onChanged()
    } catch (requestError) { setCaptureState('error'); setError(requestError.message) }
  }
  const recent = [...approved].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 3)
  return <div className="content dashboard-content">
    <div className="page-heading"><div><div className="eyebrow"><span className="live-dot"/> YOUR COMMUNITY</div><h1>{status?.broadcaster ? `Welcome back, ${status.broadcaster}` : 'Your community’s story starts here.'}</h1><p>Keep the moments that make your community yours.</p></div><button className="button secondary" onClick={() => setManualOpen((value) => !value)}><Plus size={17}/> Add memory</button></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-link" onClick={onChanged}>Retry</button></div>}
    {manualOpen && <section className="panel form-panel"><div className="section-heading"><div><h2>Add a memory</h2><p>Manual memories are immediately approved.</p></div><button className="icon-button" aria-label="Close form" onClick={() => setManualOpen(false)}>×</button></div><MemoryForm onCancel={() => setManualOpen(false)} onSaved={() => { setManualOpen(false); onChanged() }}/></section>}
    <section className="hero hero-dashboard"><div className="hero-copy"><div className="hero-tag"><Sparkles size={14}/> Community memory</div><h2>Little moments make<br/><span>your community legendary.</span></h2><p>Capture the stories, characters, and inside jokes chat will bring up again.</p><button className="button primary" onClick={capture} disabled={!status?.connected || captureState === 'working'} title={!status?.connected ? 'Connect Twitch to capture chat' : undefined}><WandSparkles size={17}/>{captureState === 'working' ? 'Capturing…' : 'Capture Recent Chat'} <ArrowUpRight size={16}/></button><div className={`hero-note ${captureState === 'error' ? 'error-text' : ''}`}>{captureState === 'empty' ? 'No recent chat in the rolling buffer yet.' : captureState === 'no-result' ? 'No meaningful moment was found. Nothing was saved.' : captureState === 'created' ? 'A new suggestion is waiting in your inbox.' : captureState === 'error' ? 'Capture failed. Try again in a moment.' : status?.connected ? 'Only you can approve a suggested memory.' : 'Connect Twitch to start collecting recent chat.'}</div></div><div className="hero-art"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="sparkle-core"><BookOpen size={34}/></div><span className="float-chip chip-one">✦ &nbsp; community moments</span><span className="float-chip chip-two">♡ &nbsp; shared history</span><span className="float-chip chip-three">✧ &nbsp; inside jokes</span></div></section>
    <div className="section-title"><div><h3>Your memory space</h3><p>A live snapshot of your community’s history.</p></div><span className={`connection-pill ${status?.connected ? 'connected' : ''}`}><i/>{status?.connected ? `${status.broadcaster} · ${status.listener}` : 'Twitch not connected'}</span></div>
    <section className="stats"><article className="stat-card"><span className="stat-icon purple"><BookOpen size={18}/></span><span className="stat-label">APPROVED MEMORIES</span><strong>{loading ? '—' : approved.length}</strong><span className="stat-foot">Ready to answer community lore</span></article><article className="stat-card"><span className="stat-icon amber"><Clock3 size={18}/></span><span className="stat-label">NEEDS YOUR REVIEW</span><strong>{loading ? '—' : pending.length}</strong><span className="stat-foot">AI suggestions waiting for you</span></article><article className="stat-card"><span className="stat-icon green"><Radio size={18}/></span><span className="stat-label">TWITCH CONNECTION</span><strong className="stat-text">{status?.connected ? 'Connected' : 'Not connected'}</strong><span className="stat-foot">{status?.listener || 'Connect to receive chat'}</span></article></section>
    <section className="panel activity-panel"><div className="section-heading"><div><h2>Recent community activity</h2><p>Your latest approved memories.</p></div></div>{loading ? <div className="state-card">Loading community activity…</div> : recent.length ? <div className="activity-list">{recent.map((memory) => <article className="activity-row" key={memory.id}><span className="activity-marker"/><div><strong>{memory.title}</strong><p>{memory.description}</p></div><time>{formatDate(memory.created_at)}</time></article>)}</div> : <div className="state-card"><div className="state-icon"><BookOpen size={20}/></div><h3>Your community’s story starts here.</h3><p>Approved memories will show up here. Capture chat or add the first one manually.</p><button className="text-link" onClick={() => setManualOpen(true)}>Create your first memory <ArrowUpRight size={15}/></button></div>}</section>
  </div>
}
