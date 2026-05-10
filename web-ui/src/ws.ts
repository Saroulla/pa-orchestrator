export type WsHandlers = {
  onToken: (text: string) => void
  onStatus: (data: unknown) => void
  onDone: () => void
  onError: (data: unknown) => void
  onEscalation: (data: unknown) => void
  onJobComplete: (data: unknown) => void
}

let socket: WebSocket | null = null

export function connect(sessionId: string, handlers: WsHandlers): void {
  if (socket) socket.close()

  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  socket = new WebSocket(`${proto}//${window.location.host}/v1/stream/${sessionId}`)

  socket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data as string) as { event: string; data: unknown }
      switch (msg.event) {
        case 'token':      handlers.onToken(msg.data as string); break
        case 'status':     handlers.onStatus(msg.data); break
        case 'done':       handlers.onDone(); break
        case 'error':      handlers.onError(msg.data); break
        case 'escalation': handlers.onEscalation(msg.data); break
        case 'job_complete': handlers.onJobComplete(msg.data); break
      }
    } catch {
      // non-JSON frames ignored
    }
  }

  socket.onerror = () => handlers.onError({ message: 'WebSocket connection error' })
}

export function disconnect(): void {
  socket?.close()
  socket = null
}
