import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Search, X } from 'lucide-react'
import MemoryCard, { dateLabel } from '../components/MemoryCard'
import { deleteMemory, getMemories } from '../services/api'

const categories = ['all', 'inside_joke', 'memorable_moment', 'community_achievement', 'recurring_character', 'stream_incident', 'community_event', 'other']

export default function Lore({ onChanged }) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      setLoading(true)
      getMemories('approved', query.trim()).then((rows) => { if (active) { setItems(rows); setError('') } })
        .catch((requestError) => { if (active) setError(requestError.message) })
        .finally(() => { if (active) setLoading(false) })
    }, 220)
    return () => { active = false; clearTimeout(timer) }
  }, [query, reload])
  const filtered = useMemo(() => items.filter((item) => category === 'all' || item.category === category)
    .sort((a, b) => new Date(b.event_time || b.created_at) - new Date(a.event_time || a.created_at)), [items, category])
  async function remove() {
    if (!selected || !window.confirm(`Delete “${selected.title}” and its saved source evidence?`)) return
    try { await deleteMemory(selected.id); setSelected(null); setReload((value) => value + 1); onChanged() }
    catch (requestError) { setError(requestError.message) }
  }
  return <div className="content page-content"><div className="page-heading"><div><div className="eyebrow">APPROVED COMMUNITY HISTORY</div><h1>Community lore</h1><p>A searchable timeline of the moments your community chose to remember.</p></div><span className="count-badge">{items.length} memories</span></div>
    <div className="lore-toolbar"><label className="search-box"><Search size={17}/><input aria-label="Search community lore" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search memories…"/></label><select aria-label="Filter by category" value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((item) => <option key={item} value={item}>{item === 'all' ? 'All categories' : item.replaceAll('_', ' ')}</option>)}</select></div>
    {error && <div className="inline-error" role="alert">{error} <button className="text-link" onClick={() => setReload((v) => v + 1)}>Retry</button></div>}
    {loading ? <div className="state-card">Searching approved memories…</div> : filtered.length ? <div className="timeline">{filtered.map((memory) => <div className="timeline-item" key={memory.id}><div className="timeline-date">{dateLabel(memory.event_time || memory.created_at)}</div><div className="timeline-pin"/><MemoryCard memory={memory} onSelect={setSelected}/></div>)}</div> : <div className="state-card large-empty"><div className="state-icon"><BookOpen size={21}/></div><h2>{query ? 'No matching lore found.' : 'Your story starts here.'}</h2><p>{query ? 'Try a different phrase or category.' : 'Approved memories will appear in this timeline.'}</p></div>}
    {selected && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null) }}><section className="detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title"><div className="modal-heading"><div><span className="category-chip">{String(selected.category).replaceAll('_', ' ')}</span><h2 id="detail-title">{selected.title}</h2></div><button className="icon-button" aria-label="Close details" onClick={() => setSelected(null)}><X size={18}/></button></div><p className="detail-description">{selected.description}</p><div className="detail-meta"><span>Event: {dateLabel(selected.event_time)}</span><span>Saved: {dateLabel(selected.created_at)}</span></div><h3>Sources and context</h3>{(selected.memory_sources || []).length ? <div className="source-list">{selected.memory_sources.map((source, index) => <blockquote key={source.id || index}><p>{source.excerpt || 'No excerpt recorded.'}</p><small>{source.source_type?.replaceAll('_', ' ') || 'Source'} · {dateLabel(source.source_time)}</small>{source.stream_url && <a href={source.stream_url} target="_blank" rel="noreferrer">Open stream source</a>}</blockquote>)}</div> : <p className="subtle">No supporting source was saved for this memory.</p>}<div className="modal-actions"><button className="button danger" onClick={remove}>Delete memory</button><button className="button secondary" onClick={() => setSelected(null)}>Close</button></div></section></div>}
  </div>
}
