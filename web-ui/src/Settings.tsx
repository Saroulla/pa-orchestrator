import React from 'react'

// ─── settings types + persistence ───────────────────────────────────────────

export interface UiSettings {
  bgColor: string
  fontColor: string
  fontSize: number
}

export const DEFAULT_SETTINGS: UiSettings = {
  bgColor:   '#FFF0E8',
  fontColor: '#1A0A02',
  fontSize:  14,
}

export function loadSettings(): UiSettings {
  try {
    const raw = localStorage.getItem('pa_ui_settings')
    if (raw) return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<UiSettings>) }
  } catch {
    // ignore corrupt data
  }
  return { ...DEFAULT_SETTINGS }
}

export function saveSettings(s: UiSettings): void {
  localStorage.setItem('pa_ui_settings', JSON.stringify(s))
}

// ─── local colours (mirrors C in Terminal.tsx) ───────────────────────────────

const SURFACE = '#FDEAE0'
const BORDER  = '#E8C8B4'
const TEXT    = '#1A0A02'
const MUTED   = '#8C6054'

// ─── component ───────────────────────────────────────────────────────────────

export default function SettingsPanel({
  settings,
  onChange,
  onClose,
}: {
  settings: UiSettings
  onChange: (s: UiSettings) => void
  onClose: () => void
}) {
  function set<K extends keyof UiSettings>(key: K, value: UiSettings[K]) {
    onChange({ ...settings, [key]: value })
  }

  // Transparent overlay sits behind the panel; clicking it closes the drawer.
  const overlayStyle: React.CSSProperties = {
    position: 'absolute',
    inset:    0,
    zIndex:   19,
  }

  const panelStyle: React.CSSProperties = {
    position:        'absolute',
    top:             0,
    right:           0,
    bottom:          0,
    width:           '260px',
    backgroundColor: SURFACE,
    borderLeft:      `1px solid ${BORDER}`,
    color:           TEXT,
    display:         'flex',
    flexDirection:   'column',
    padding:         '20px 16px 16px',
    gap:             '20px',
    zIndex:          20,
    fontFamily:      '"Cascadia Code","Fira Code","Courier New",monospace',
    fontSize:        '13px',
    overflowY:       'auto',
  }

  const rowStyle: React.CSSProperties = {
    display:        'flex',
    justifyContent: 'space-between',
    alignItems:     'center',
    gap:            '8px',
  }

  const labelStyle: React.CSSProperties = {
    color:      TEXT,
    flexShrink: 0,
  }

  const colorInputStyle: React.CSSProperties = {
    border:       `1px solid ${BORDER}`,
    borderRadius: '4px',
    background:   '#FAF8F1',
    cursor:       'pointer',
    width:        '44px',
    height:       '28px',
    padding:      '2px',
    flexShrink:   0,
  }

  const rangeStyle: React.CSSProperties = {
    width:       '110px',
    cursor:      'pointer',
    accentColor: '#166534',
  }

  const resetStyle: React.CSSProperties = {
    marginTop:    'auto',
    padding:      '6px 12px',
    border:       `1px solid ${BORDER}`,
    borderRadius: '4px',
    background:   'transparent',
    color:        MUTED,
    cursor:       'pointer',
    fontFamily:   'inherit',
    fontSize:     'inherit',
    width:        '100%',
    textAlign:    'center',
  }

  return (
    <>
      {/* click-outside area */}
      <div style={overlayStyle} onClick={onClose} />

      {/* settings drawer */}
      <div style={panelStyle}>
        <div style={{ fontSize: '14px', fontWeight: 'bold', color: TEXT }}>
          Settings
        </div>

        <div style={rowStyle}>
          <span style={labelStyle}>Background</span>
          <input
            type="color"
            value={settings.bgColor}
            style={colorInputStyle}
            onChange={e => set('bgColor', e.target.value)}
          />
        </div>

        <div style={rowStyle}>
          <span style={labelStyle}>Font color</span>
          <input
            type="color"
            value={settings.fontColor}
            style={colorInputStyle}
            onChange={e => set('fontColor', e.target.value)}
          />
        </div>

        <div style={rowStyle}>
          <span style={labelStyle}>Font size&nbsp;{settings.fontSize}px</span>
          <input
            type="range"
            min={11}
            max={22}
            step={1}
            value={settings.fontSize}
            style={rangeStyle}
            onChange={e => set('fontSize', Number(e.target.value))}
          />
        </div>

        <button style={resetStyle} onClick={() => onChange({ ...DEFAULT_SETTINGS })}>
          Reset to defaults
        </button>
      </div>
    </>
  )
}
