import { Check, Clock3, Pencil, Quote, X } from 'lucide-react'

export function dateLabel(value) {
  if (!value) return 'Date not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Date not recorded' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export default function MemoryCard({ memory, actions, onSelect }) {
  const sources = memory.memory_sources || memory.sources || []
  return <article className="memory-card">
    <div className="memory-card-top"><span className="category-chip">{String(memory.category || 'other').replaceAll('_', ' ')}</span><span className={`status-chip ${memory.status}`}>{memory.status}</span></div>
    <button className="memory-title-button" onClick={() => onSelect?.(memory)}>{memory.title}</button>
    <p className="memory-description">{memory.description}</p>
    <div className="memory-meta"><span><Clock3 size={14}/>{dateLabel(memory.event_time || memory.created_at)}</span>{memory.confidence != null && <span>Confidence {Math.round(Number(memory.confidence) * 100)}%</span>}</div>
    {sources.length > 0 && <div className="evidence-block"><div className="evidence-label"><Quote size={14}/> Supporting context</div>{sources.map((source, index) => <blockquote key={source.id || index}>{source.excerpt || source.source_excerpt || 'Source noted'}{source.source_time && <time>{dateLabel(source.source_time)}</time>}</blockquote>)}</div>}
    {actions && <div className="memory-actions">{actions.includes('approve') && <button className="button primary small" onClick={() => actions.onApprove(memory)}><Check size={15}/> Approve</button>}{actions.includes('reject') && <button className="button danger small" onClick={() => actions.onReject(memory)}><X size={15}/> Reject</button>}{actions.includes('edit') && <button className="button secondary small" onClick={() => actions.onEdit(memory)}><Pencil size={15}/> Edit</button>}</div>}
  </article>
}
