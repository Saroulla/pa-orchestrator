import { useEffect, useMemo, useState } from 'react'
import Login from './Login'
import Terminal from './Terminal'

function makeSessionId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 16)
  }
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

type AuthState = 'checking' | 'authenticated' | 'unauthenticated'

export default function App() {
  const sessionId = useMemo(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('session_id') ?? makeSessionId()
  }, [])

  const [authState, setAuthState] = useState<AuthState>('checking')

  useEffect(() => {
    fetch('/auth/check')
      .then(r => setAuthState(r.ok ? 'authenticated' : 'unauthenticated'))
      .catch(() => setAuthState('unauthenticated'))
  }, [])

  if (authState === 'checking') {
    return null
  }

  if (authState === 'unauthenticated') {
    return <Login onAuthenticated={() => setAuthState('authenticated')} />
  }

  return (
    <div style={{ width: '100vw', height: '100vh', overflow: 'hidden' }}>
      <Terminal sessionId={sessionId} />
    </div>
  )
}
