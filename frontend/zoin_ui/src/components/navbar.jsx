import { Link } from 'react-router-dom'
import { useSession } from '../lib/session'
import placeholder from '../assets/profilep.png'
export default function Navbar({ theme, toggleTheme }) {
  const { user, profile } = useSession()
  return <header className="app-header">
    {user ? <Link className="profile" to="/profile" aria-label="Open profile"><img src={profile?.avatar_url || placeholder} alt="" /></Link> : <span />}
    <div className="header-end"><Link className="brand brand-link" to={user ? '/home' : '/login'}>zoin</Link>
      <button className="theme-toggle" type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}>{theme === 'light' ? 'Dark mode' : 'Light mode'}</button>
    </div>
  </header>
}
