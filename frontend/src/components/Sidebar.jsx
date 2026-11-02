import { BookOpen, LayoutDashboard, Radio, Settings, Sparkles } from 'lucide-react'

const links = [
  [LayoutDashboard, 'Overview', 'overview'],
  [Sparkles, 'Memory inbox', 'inbox'],
  [BookOpen, 'Community lore', 'lore'],
  [Radio, 'Settings', 'settings'],
]

export default function Sidebar({ page, onNavigate, pendingCount = 0, channelName }) {
  return <aside className="sidebar">
    <a className="brand" href="#overview" onClick={() => onNavigate('overview')}><span className="brand-mark"><Sparkles size={19} /></span><span>stream<span className="brand-light">memory</span></span></a>
    <div className="workspace-label">YOUR WORKSPACE</div>
    <nav aria-label="Desktop sidebar navigation">{links.map(([Icon, label, key]) => <button key={key} type="button" className={`nav-link ${page === key ? 'active' : ''}`} onClick={() => onNavigate(key)}><Icon size={18} /><span>{label}</span>{key === 'inbox' && pendingCount > 0 && <small>{pendingCount}</small>}</button>)}</nav>
    <div className="sidebar-bottom"><div className="channel-avatar">{channelName ? channelName.slice(0, 1).toUpperCase() : 'S'}</div><div><strong>{channelName || 'Your channel'}</strong><span>{channelName ? 'Twitch connected' : 'Not connected'}</span></div><button className="icon-button" aria-label="Open settings" onClick={() => onNavigate('settings')}><Settings size={17} /></button></div>
  </aside>
}
