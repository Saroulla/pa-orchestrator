export type KnownCommand = '@PA' | '@CTO' | '@cost' | '@Desktop' | '@rebuild-plan'

export type CommandResult = {
  command: KnownCommand | null
  rest: string
}

const COMMANDS: KnownCommand[] = ['@PA', '@CTO', '@cost', '@Desktop', '@rebuild-plan']

export function detectCommand(text: string): CommandResult {
  const trimmed = text.trimStart()

  // \@ escape → treat as literal text, strip the backslash
  if (trimmed.startsWith('\\@')) {
    return { command: null, rest: trimmed.slice(1) }
  }

  const spaceIdx = trimmed.search(/\s/)
  const first = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx)
  const rest = spaceIdx === -1 ? '' : trimmed.slice(spaceIdx + 1)

  for (const cmd of COMMANDS) {
    if (first === cmd) return { command: cmd, rest }
  }

  return { command: null, rest: trimmed }
}
