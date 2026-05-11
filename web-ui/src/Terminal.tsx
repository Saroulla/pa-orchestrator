import { useCallback, useEffect, useRef, useState } from 'react'
import { connect, disconnect } from './ws'
import { detectCommand } from './parser'
import SettingsPanel, { loadSettings, saveSettings } from './Settings'
import type { UiSettings } from './Settings'

// ─── types ───────────────────────────────────────────────────────────────────

type Mode = 'PA' | 'DESKTOP'
type LineKind = 'user' | 'assistant' | 'error' | 'escalation' | 'status'

interface Line {
  id: number
  kind: LineKind
  text: string
  speaker?: 'user' | 'pa'
  options?: Record<string, string>
}

interface TerminalProps {
  sessionId: string
}

// ─── colours (peach palette — mirrors VS Code workbench.colorCustomizations) ──

const C = {
  bg:           '#FFF0E8',  // warm peach white — primary background
  surface:      '#FDEAE0',  // slightly deeper peach — input row, panels
  border:       '#E8C8B4',  // muted peach-brown dividers
  text:         '#1A0A02',  // near-black warm — body text (~17:1 contrast on bg)
  muted:        '#8C6054',  // warm brown — status lines, timestamps
  pa:           '#166534',  // dark green — PA/orchestrator response text
  red:          '#B91C1C',  // dark red — errors
  amber:        '#92400E',  // dark amber — escalation text
  escalationBg: '#FEF2E8',  // pale peach wash — escalation block background
}

// ─── agent palette (deterministic color assignment for future subagents) ─────

const AGENT_PALETTE = ['#1E40AF', '#7C2D12', '#4C1D95', '#065F46', '#1E3A5F']

/** Stable color for a named subagent. All entries have ≥7:1 contrast on #FFF0E8. */
export function agentColor(agentName: string): string {
  let h = 0
  for (const ch of agentName) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return AGENT_PALETTE[h % AGENT_PALETTE.length]
}

// ─── mode helpers ─────────────────────────────────────────────────────────────

function modeAccent(mode: Mode): string {
  switch (mode) {
    case 'DESKTOP': return C.muted
    default:        return C.pa
  }
}

// ─── line styling ─────────────────────────────────────────────────────────────

const SL: React.CSSProperties = {
  lineHeight: '1.5',
  wordBreak:  'break-word',
  whiteSpace: 'pre-wrap',
}

function lineStyle(line: Line, fontColor: string = C.text): React.CSSProperties {
  if (line.kind === 'error')
    return { ...SL, color: C.red, backgroundColor: 'rgba(185,28,28,0.06)', padding: '2px 6px', borderRadius: '3px' }
  if (line.kind === 'status')
    return { ...SL, color: C.muted, fontSize: '12px' }
  if (line.kind === 'escalation')
    return SL
  if (line.speaker === 'pa')
    return { ...SL, color: C.pa }
  return { ...SL, color: fontColor }
}

// ─── component ───────────────────────────────────────────────────────────────

let _id = 0
const nextId = () => ++_id

export default function Terminal({ sessionId }: TerminalProps) {
  const [settings, setSettings]       = useState<UiSettings>(loadSettings)
  const [showSettings, setShowSettings] = useState(false)
  const [lines, setLines]             = useState<Line[]>(() => [
    { id: nextId(), kind: 'status', text: `session: ${sessionId}` },
    { id: nextId(), kind: 'status', text: 'ready — type @cost to check spend' },
  ])
  const [pending, setPending]         = useState('')
  const [mode, setMode]               = useState<Mode>('PA')
  const [input, setInput]             = useState('')
  const [busy, setBusy]               = useState(false)
  const scrollRef                     = useRef<HTMLDivElement>(null)
  const pendingRef                    = useRef('')
  const modeRef                       = useRef<Mode>('PA')

  useEffect(() => { pendingRef.current = pending }, [pending])
  useEffect(() => { modeRef.current = mode }, [mode])

  const addLine = useCallback(
    (kind: LineKind, text: string, options?: Record<string, string>, speaker?: 'user' | 'pa') => {
      setLines(prev => [...prev, { id: nextId(), kind, text, options, speaker }])
    },
    [],
  )

  // auto-scroll to bottom on new content
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, pending])

  // WebSocket lifecycle
  useEffect(() => {
    connect(sessionId, {
      onToken: (text) => setPending(prev => prev + text),
      onStatus: (data) => {
        const msg = typeof data === 'string' ? data : JSON.stringify(data)
        addLine('status', msg)
      },
      onDone: () => {
        const text = pendingRef.current
        if (text) {
          addLine('assistant', text, undefined, 'pa')
        }
        setPending('')
        setBusy(false)
      },
      onError: (data) => {
        const msg =
          data && typeof data === 'object' && 'message' in data
            ? String((data as Record<string, unknown>).message)
            : JSON.stringify(data)
        setPending('')
        addLine('error', `[error] ${msg}`)
        setBusy(false)
      },
      onEscalation: (data) => {
        const d = data as { message?: string; options?: Record<string, string> }
        addLine('escalation', d.message ?? 'action required', d.options)
        setBusy(false)
      },
      onJobComplete: (data) => {
        const d = data as { summary?: string }
        addLine('status', `✓ job complete${d.summary ? ': ' + d.summary : ''}`)
      },
    })
    return disconnect
  }, [sessionId, addLine])

  const submit = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return

    const { command, rest } = detectCommand(text)
    const outgoing = command ? (rest ? `${command} ${rest}` : command) : text

    setInput('')
    addLine('user', text, undefined, 'user')
    setBusy(true)

    try {
      const res = await fetch('/v1/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ session_id: sessionId, text: outgoing, channel: 'web' }),
      })

      if (!res.ok) {
        const body = await res.text()
        addLine('error', `HTTP ${res.status}: ${body}`)
        setBusy(false)
        return
      }

      const json = await res.json() as { mode?: string; response?: string }
      if (json.mode) setMode(json.mode as Mode)

      // Non-streaming fallback: backend sent a full response before WS token stream
      if (json.response && !pendingRef.current) {
        addLine('assistant', json.response, undefined, 'pa')
        setBusy(false)
      }
      // Otherwise WS onDone commits the streamed text and clears busy
    } catch (err) {
      addLine('error', String(err))
      setBusy(false)
    }
  }, [input, busy, sessionId, addLine])

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }, [submit])

  function handleSettingsChange(s: UiSettings) {
    setSettings(s)
    saveSettings(s)
  }

  // ── dynamic styles driven by settings ──────────────────────────────────────

  const accent = modeAccent(mode)

  const containerStyle: React.CSSProperties = {
    display:         'flex',
    flexDirection:   'column',
    height:          '100%',
    width:           '100%',
    position:        'relative',
    backgroundColor: settings.bgColor,
    fontFamily:      '"Cascadia Code","Fira Code","Courier New",monospace',
    fontSize:        `${settings.fontSize}px`,
    color:           settings.fontColor,
    boxSizing:       'border-box',
  }

  const scrollbackStyle: React.CSSProperties = {
    flex:          1,
    overflowY:     'auto',
    padding:       '16px',
    display:       'flex',
    flexDirection: 'column',
    gap:           '3px',
  }

  const inputRowStyle: React.CSSProperties = {
    display:         'flex',
    alignItems:      'center',
    padding:         '8px 16px',
    borderTop:       `1px solid ${C.border}`,
    gap:             '8px',
    backgroundColor: C.surface,
    flexShrink:      0,
  }

  const promptStyle: React.CSSProperties = {
    color:      accent,
    flexShrink: 0,
    userSelect: 'none',
  }

  const inputStyle: React.CSSProperties = {
    flex:       1,
    background: 'transparent',
    border:     'none',
    outline:    'none',
    color:      settings.fontColor,
    fontFamily: 'inherit',
    fontSize:   'inherit',
    caretColor: accent,
  }

  const gearStyle: React.CSSProperties = {
    position:        'absolute',
    top:             '8px',
    right:           '12px',
    zIndex:          10,
    background:      'none',
    border:          'none',
    color:           C.muted,
    cursor:          'pointer',
    fontSize:        '16px',
    lineHeight:      '1',
    padding:         '2px 4px',
    userSelect:      'none',
    borderRadius:    '3px',
  }

  const userPrefixStyle: React.CSSProperties = {
    color:       accent,
    marginRight: '6px',
    userSelect:  'none',
  }

  const cursorStyle: React.CSSProperties = {
    color:     accent,
    animation: 'blink 1s step-end infinite',
  }

  const promptLabel = busy ? '...' : `[${mode}]>`

  return (
    <div style={containerStyle}>

      {/* gear — opens / closes settings panel */}
      <button
        style={gearStyle}
        onClick={() => setShowSettings(v => !v)}
        aria-label="Settings"
        title="Settings"
      >
        ⚙
      </button>

      {/* settings drawer */}
      {showSettings && (
        <SettingsPanel
          settings={settings}
          onChange={handleSettingsChange}
          onClose={() => setShowSettings(false)}
        />
      )}

      {/* scrollback */}
      <div ref={scrollRef} style={scrollbackStyle}>
        {lines.map(line =>
          line.kind === 'escalation' ? (
            <div key={line.id} style={lineStyle(line, settings.fontColor)}>
              <div style={{
                borderLeft:      `3px solid ${C.amber}`,
                paddingLeft:     '12px',
                paddingTop:      '4px',
                paddingBottom:   '4px',
                backgroundColor: C.escalationBg,
                color:           C.amber,
                borderRadius:    '0 3px 3px 0',
              }}>
                <div>{line.text}</div>
                {line.options && Object.entries(line.options).map(([k, v]) => (
                  <div key={k} style={{ marginTop: '3px', color: C.amber, opacity: 0.85 }}>
                    ({k}) {v}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div key={line.id} style={lineStyle(line, settings.fontColor)}>
              {line.kind === 'user' && (
                <span style={userPrefixStyle}>[{mode}]&gt;</span>
              )}
              {line.text}
            </div>
          )
        )}

        {/* streaming tokens — color matches current mode */}
        {pending && (
          <div style={{ ...SL, color: accent }}>
            {pending}<span style={cursorStyle}>▋</span>
          </div>
        )}
      </div>

      {/* input row */}
      <div style={inputRowStyle}>
        <span style={promptStyle}>{promptLabel}</span>
        <input
          style={inputStyle}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
          disabled={busy}
          placeholder={busy ? '' : 'type a message…'}
          spellCheck={false}
          autoComplete="off"
        />
      </div>
    </div>
  )
}
