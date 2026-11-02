import { useState } from 'react'
import { createMemory, updateMemory } from '../services/api'

const categories = ['inside_joke', 'memorable_moment', 'community_achievement', 'recurring_character', 'stream_incident', 'community_event', 'other']

export default function MemoryForm({ initial = {}, memoryId, onSaved, onCancel }) {
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  async function submit(event) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const data = Object.fromEntries(form)
    if (data.event_time) data.event_time = new Date(data.event_time).toISOString()
    for (const field of ['event_time', 'source_note', 'stream_url']) if (!data[field]) delete data[field]
    setSaving(true)
    setError('')
    try {
      if (memoryId) await updateMemory(memoryId, { title: data.title, description: data.description,
        category: data.category, event_time: data.event_time || null })
      else await createMemory(data)
      onSaved?.()
    } catch (requestError) { setError(requestError.message) }
    finally { setSaving(false) }
  }
  return <form className="memory-form" onSubmit={submit}>
    <label>Title<input name="title" required maxLength="120" defaultValue={initial.title || ''} placeholder="A moment worth remembering" /></label>
    <label>Description<textarea name="description" required maxLength="2000" defaultValue={initial.description || ''} placeholder="What happened in your community?" /></label>
    <label>Category<select name="category" defaultValue={initial.category || 'memorable_moment'}>{categories.map((category) => <option key={category} value={category}>{category.replaceAll('_', ' ')}</option>)}</select></label>
    <label>Event date<input name="event_time" type="datetime-local" defaultValue={initial.event_time ? new Date(initial.event_time).toISOString().slice(0, 16) : ''} /></label>
    {!memoryId && <><label>Source note <span className="optional">Optional</span><textarea name="source_note" maxLength="2000" placeholder="Add context for this memory" /></label><label>Stream URL <span className="optional">Optional</span><input name="stream_url" type="url" maxLength="500" placeholder="https://twitch.tv/..." /></label></>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="form-actions">{onCancel && <button className="button secondary" type="button" onClick={onCancel}>Cancel</button>}<button className="button primary" disabled={saving}>{saving ? 'Saving…' : memoryId ? 'Save changes' : 'Save memory'}</button></div>
  </form>
}
