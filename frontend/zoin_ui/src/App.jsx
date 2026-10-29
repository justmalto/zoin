import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import SessionProvider from './components/SessionProvider'
import ProtectedRoute from './components/ProtectedRoute'
import Navbar from './components/navbar'
import { useSession } from './lib/session'
import Home from './pages/home'
import Login from './pages/login'
import Register from './pages/register'
import CheckEmail from './pages/checkEmail'
import Automation from './pages/automation'
import Profile from './pages/profile'
import JobDetails from './pages/jobDetails'

function Shell({ theme, toggleTheme }) {
  const location = useLocation(), { user } = useSession()
  const background = location.state?.backgroundLocation
  const protect = child => <ProtectedRoute>{child}</ProtectedRoute>
  return <><Navbar theme={theme} toggleTheme={toggleTheme} /><div key={user?.id || 'guest'}>
    <Routes location={background || location}>
      <Route path="/" element={<Navigate to={user ? '/home' : '/login'} replace />} />
      <Route path="/check-email" element={<CheckEmail />} /><Route path="/login" element={<Login />} /><Route path="/register" element={<Register />} />
      <Route path="/automation" element={protect(<Automation />)} /><Route path="/home" element={protect(<Home />)} /><Route path="/profile" element={protect(<Profile />)} />
      <Route path="/jobs/new" element={protect(<JobDetails />)} /><Route path="/jobs/:id" element={protect(<JobDetails />)} />
      <Route path="*" element={<main className="screen"><h1>Page not found</h1><Link to="/home">Go to applications</Link></main>} />
    </Routes>
    {background && <Routes><Route path="/jobs/new" element={protect(<JobDetails modal />)} /><Route path="/jobs/:id" element={protect(<JobDetails modal />)} /></Routes>}
  </div></>
}

export default function App() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('zoin-theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('zoin-theme', theme) } catch { /* Theme still works without local storage. */ }
  }, [theme])
  return <SessionProvider><Shell theme={theme} toggleTheme={() => setTheme(old => old === 'light' ? 'dark' : 'light')} /></SessionProvider>
}
