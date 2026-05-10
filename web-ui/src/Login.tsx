import { type FormEvent, useState } from 'react'

const C = {
  bg:      '#FFF0E8',
  surface: '#FDEAE0',
  border:  '#E8C8B4',
  text:    '#1A0A02',
  muted:   '#8C6054',
  pa:      '#166534',
  red:     '#B91C1C',
}

interface LoginProps {
  onAuthenticated: () => void
}

export default function Login({ onAuthenticated }: LoginProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (data.ok) {
        onAuthenticated()
      } else {
        setError(data.error ?? 'Login failed')
      }
    } catch {
      setError('Cannot reach server')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      width: '100vw', height: '100vh',
      background: C.bg,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
    }}>
      <form
        onSubmit={handleSubmit}
        style={{
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          padding: '2rem 2.5rem',
          display: 'flex', flexDirection: 'column', gap: '1rem',
          minWidth: 300,
        }}
      >
        <div style={{ color: C.pa, fontWeight: 700, fontSize: '1.1rem', marginBottom: 4 }}>
          [PA]&gt; identify yourself
        </div>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ color: C.muted, fontSize: '0.8rem' }}>username</span>
          <input
            type="text"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={e => setUsername(e.target.value)}
            style={inputStyle}
          />
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ color: C.muted, fontSize: '0.8rem' }}>password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            style={inputStyle}
          />
        </label>

        {error && (
          <div style={{ color: C.red, fontSize: '0.85rem' }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={loading || !username || !password}
          style={{
            marginTop: 4,
            padding: '0.5rem 1rem',
            background: loading ? C.border : C.pa,
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            fontFamily: 'inherit',
            fontSize: '0.9rem',
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? 'checking...' : 'login'}
        </button>
      </form>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  background: '#FFF0E8',
  border: `1px solid #E8C8B4`,
  borderRadius: 4,
  padding: '0.4rem 0.6rem',
  fontFamily: 'inherit',
  fontSize: '0.9rem',
  color: '#1A0A02',
  outline: 'none',
}
