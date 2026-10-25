import { atom } from 'nanostores'

import { capitalize } from '@/lib/text'

export type SubagentStatus = 'completed' | 'failed' | 'interrupted' | 'queued' | 'running'
export type SubagentStreamKind = 'progress' | 'summary' | 'thinking' | 'tool'

export interface SubagentStreamEntry {
  at: number
  isError?: boolean
  kind: SubagentStreamKind
  text: string
}

export interface SubagentProgress {
  id: string
  parentId: null | string
  goal: string
  /** The child's own stored session id — lets UIs open its session window. */
  sessionId?: string
  model?: string
  status: SubagentStatus
  taskCount: number
  taskIndex: number
  startedAt: number
  updatedAt: number
  durationSeconds?: number
  costUsd?: number
  inputTokens?: number
  outputTokens?: number
  toolCount?: number
  filesRead: string[]
  filesWritten: string[]
  stream: SubagentStreamEntry[]
  summary?: string
  /** Active tool while running — cleared on terminal status. */
  currentTool?: string
}

export interface SubagentNode extends SubagentProgress {
  children: SubagentNode[]
}

export type SubagentPayload = Record<string, unknown>

const TERMINAL: ReadonlySet<SubagentStatus> = new Set(['completed', 'failed', 'interrupted'])
const MAX_STREAM = 24

/** A non-terminal row with no event for this long is not proven alive. Live
 *  subagents stream progress/thinking/tool events continuously (each refreshes
 *  updatedAt via toProgress), so a genuine worker never trips this; a row whose
 *  subagent.complete was lost (gateway death, socket drop, app sleep) does.
 *  This is the fail-safe bound under the reconnect reconciliation below: even
 *  with no reconnect event ever firing, the counter self-corrects to truth
 *  within one TTL. */
export const RUNNING_STALE_MS = 10 * 60_000

export const isStaleSubagent = (item: SubagentProgress, now = Date.now()) =>
  !TERMINAL.has(item.status) && now - item.updatedAt > RUNNING_STALE_MS
const PREVIEW_MAX = 220
const TOOL_PREVIEW_MAX = 96

export const $subagentsBySession = atom<Record<string, SubagentProgress[]>>({})

const isStr = (v: unknown): v is string => typeof v === 'string'
const str = (v: unknown) => (isStr(v) ? v : '')
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
const strList = (v: unknown) => (Array.isArray(v) ? v.filter(isStr) : [])

const asStatus = (v: unknown, terminalEvent = false): SubagentStatus => {
  if (v === 'completed' || v === 'failed' || v === 'interrupted') {
    return v
  }

  if (v === 'timeout' || v === 'error') {
    return 'failed'
  }

  if (v === 'cancelled' || v === 'canceled') {
    return 'interrupted'
  }

  // Fail closed on completion: a subagent.complete event is terminal by
  // definition, so an unrecognized (or still-active 'queued'/'running')
  // status must render as a failure rather than leave a dead row spinning
  // as 'running' forever. Live events keep the lenient fallback.
  if (terminalEvent) {
    return 'failed'
  }

  return v === 'queued' ? v : 'running'
}

const compact = (text: string, max = PREVIEW_MAX) => {
  const line = text.replace(/\s+/g, ' ').trim()

  if (!line) {
    return ''
  }

  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

const toolLabel = (name: string) => name.split('_').filter(Boolean).map(capitalize).join(' ') || name

const formatTool = (name: string, preview = '') => {
  const snippet = compact(preview, TOOL_PREVIEW_MAX)

  return snippet ? `${toolLabel(name)}("${snippet}")` : toolLabel(name)
}

interface TailEntry {
  isError?: boolean
  preview?: string
  tool?: string
}

const asTail = (v: unknown): TailEntry[] =>
  Array.isArray(v)
    ? v
        .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
        .map(item => ({
          isError: item.is_error === true,
          preview: str(item.preview) || undefined,
          tool: str(item.tool) || undefined
        }))
    : []

const idOf = (p: SubagentPayload) =>
  str(p.subagent_id) || `${str(p.parent_id) || 'root'}:${num(p.task_index) ?? 0}:${str(p.goal)}`

const appendStream = (stream: SubagentStreamEntry[], entry: SubagentStreamEntry) => {
  const last = stream.at(-1)

  if (last?.kind === entry.kind && last.text === entry.text && last.isError === entry.isError) {
    return stream
  }

  return [...stream, entry].slice(-MAX_STREAM)
}

// The backend sends no summary on a hard child timeout (only a preview like
// "Timed out after 612.3s" + duration_seconds). Synthesize it so the terminal
// row explains why it failed instead of rendering as a bare failure.
const timeoutSummary = (payload: SubagentPayload): string => {
  const seconds = num(payload.duration_seconds)

  return str(payload.status) === 'timeout' ? `Timed out after ${seconds ?? '?'}s` : ''
}

function streamFromPayload(
  payload: SubagentPayload,
  status: SubagentStatus,
  eventType: string,
  at: number
): SubagentStreamEntry[] {
  const out: SubagentStreamEntry[] = []
  const tool = str(payload.tool_name)
  const preview = str(payload.tool_preview) || str(payload.text)
  const text = compact(str(payload.text) || preview)

  for (const tail of asTail(payload.output_tail)) {
    const line = tail.tool ? formatTool(tail.tool, tail.preview ?? '') : compact(tail.preview ?? '')

    if (line) {
      out.push({ at, isError: tail.isError, kind: tail.tool ? 'tool' : 'progress', text: line })
    }
  }

  if (tool) {
    out.push({ at, isError: !!payload.error, kind: 'tool', text: formatTool(tool, preview) })
  }

  if (eventType === 'subagent.progress' && text) {
    out.push({ at, isError: !!payload.error, kind: 'progress', text })
  }

  if (eventType === 'subagent.thinking' && text) {
    out.push({ at, kind: 'thinking', text })
  }

  const summary = compact(str(payload.summary) || str(payload.text) || timeoutSummary(payload))

  if (TERMINAL.has(status) && summary) {
    out.push({ at, isError: status === 'failed', kind: 'summary', text: summary })
  }

  return out
}

function toProgress(payload: SubagentPayload, prev: SubagentProgress | undefined, eventType = ''): SubagentProgress {
  const at = Date.now()
  const status = asStatus(payload.status, eventType === 'subagent.complete')
  const tool = str(payload.tool_name)
  const stream = streamFromPayload(payload, status, eventType, at).reduce(appendStream, prev?.stream ?? [])
  const filesRead = strList(payload.files_read)
  const filesWritten = strList(payload.files_written)

  return {
    id: prev?.id ?? idOf(payload),
    parentId: str(payload.parent_id) || prev?.parentId || null,
    goal: str(payload.goal) || prev?.goal || 'Subagent',
    sessionId: str(payload.child_session_id) || prev?.sessionId,
    model: str(payload.model) || prev?.model,
    status,
    taskCount: num(payload.task_count) ?? prev?.taskCount ?? 1,
    taskIndex: num(payload.task_index) ?? prev?.taskIndex ?? 0,
    startedAt: prev?.startedAt ?? at,
    updatedAt: at,
    durationSeconds: num(payload.duration_seconds) ?? prev?.durationSeconds,
    costUsd: num(payload.cost_usd) ?? prev?.costUsd,
    inputTokens: num(payload.input_tokens) ?? prev?.inputTokens,
    outputTokens: num(payload.output_tokens) ?? prev?.outputTokens,
    toolCount: num(payload.tool_count) ?? prev?.toolCount,
    filesRead: filesRead.length ? filesRead : (prev?.filesRead ?? []),
    filesWritten: filesWritten.length ? filesWritten : (prev?.filesWritten ?? []),
    stream,
    summary: str(payload.summary) || timeoutSummary(payload) || prev?.summary || undefined,
    currentTool: TERMINAL.has(status) ? undefined : tool || prev?.currentTool
  }
}

export function clearSessionSubagents(sid: string) {
  const map = $subagentsBySession.get()

  if (!(sid in map)) {
    return
  }

  const { [sid]: _drop, ...rest } = map
  $subagentsBySession.set(rest)
}

/**
 * Prune terminal-status subagent rows for a session, leaving running/queued
 * entries untouched. Used at the `message.start` boundary in the desktop
 * message-stream hook so that the *previous* turn's finished rows get flushed
 * from the display while background subagents that outlived the spawning turn
 * remain visible (and still accept late progress/complete events).
 *
 * Distinct from `clearSessionSubagents` (used by the Stop action, which
 * genuinely cancels running subagents and so should drop them all) and from
 * `pruneDelegateFallbackSubagents` (which filters by id prefix to remove
 * placeholder rows once the real native event arrives).
 */
export function pruneFinishedSessionSubagents(sid: string) {
  const map = $subagentsBySession.get()
  const list = map[sid]

  if (!list?.length) {
    return
  }

  const next = list.filter(item => item.status === 'running' || item.status === 'queued')

  if (next.length === list.length) {
    return
  }

  $subagentsBySession.set({ ...map, [sid]: next })
}

export function pruneDelegateFallbackSubagents(sid: string) {
  const map = $subagentsBySession.get()
  const list = map[sid]

  if (!list?.length) {
    return
  }

  const next = list.filter(item => !item.id.startsWith('delegate-tool:'))

  if (next.length === list.length) {
    return
  }

  $subagentsBySession.set({ ...map, [sid]: next })
}

export function upsertSubagent(sid: string, payload: SubagentPayload, createIfMissing = true, eventType?: string) {
  const map = $subagentsBySession.get()
  const list = map[sid] ?? []
  const id = idOf(payload)
  const idx = list.findIndex(item => item.id === id)

  if (idx < 0 && !createIfMissing) {
    return
  }

  const prev = idx >= 0 ? list[idx] : undefined

  if (prev && TERMINAL.has(prev.status)) {
    return
  }

  const next = toProgress(payload, prev, eventType)
  const nextList = idx >= 0 ? list.map(item => (item.id === id ? next : item)) : [...list, next]

  $subagentsBySession.set({ ...map, [sid]: nextList })
  scheduleStaleSweep()
}

// ---------------------------------------------------------------------------
// Staleness contract — why this exists: the status-bar indicator and the
// agents panel are projections of this store, and this store is event-sourced.
// A subagent whose terminal event is lost (gateway respawn re-mints ids, so
// the completion publish lands under a dead id; socket drop; laptop sleep)
// would otherwise spin as 'running' for the life of the app — the exact bug
// that showed the founder "12 agents running" while nothing ran (the panel
// header counted every row ever seen). Two bounds keep the number honest:
//
//   1. dropStaleSubagents — TTL expiry. A non-terminal row silent past
//      RUNNING_STALE_MS is dropped (not marked failed: the resurrection guard
//      in upsertSubagent makes 'failed' permanent, which would hide a worker
//      that is genuinely alive and re-announces on its next event).
//   2. dropUnfinishedSubagentsOnReconnect — same doctrine as
//      reconcileBusyStatesOnReconnect (session-states.ts): a respawned backend
//      can never deliver terminal events for pre-drop rows, so unfinished rows
//      are dropped at reconnect. A live worker re-asserts itself on its next
//      progress event (createIfMissing); a dead one is gone. Direction of
//      failure is deliberate: at worst the row blinks once.
// ---------------------------------------------------------------------------

let staleTimer: ReturnType<typeof setTimeout> | null = null

function oldestLiveUpdatedAt(): number | null {
  let oldest: number | null = null

  for (const list of Object.values($subagentsBySession.get())) {
    for (const item of list) {
      if (TERMINAL.has(item.status)) {
        continue
      }

      if (oldest === null || item.updatedAt < oldest) {
        oldest = item.updatedAt
      }
    }
  }

  return oldest
}

function scheduleStaleSweep() {
  if (typeof window === 'undefined' || staleTimer !== null) {
    return
  }

  const oldest = oldestLiveUpdatedAt()

  if (oldest === null) {
    return
  }

  const delay = Math.max(0, oldest + RUNNING_STALE_MS - Date.now())
  staleTimer = setTimeout(() => {
    staleTimer = null
    dropStaleSubagents()
  }, delay)
}

/** Drop every non-terminal row silent past RUNNING_STALE_MS, across all
 *  sessions. Returns the number of rows dropped. Reschedules itself while any
 *  non-terminal row remains, so the store can never hold an unbounded set of
 *  phantom 'running' rows. */
export function dropStaleSubagents(now = Date.now()): number {
  if (staleTimer !== null) {
    clearTimeout(staleTimer)
    staleTimer = null
  }

  const map = $subagentsBySession.get()
  let dropped = 0
  const next: Record<string, SubagentProgress[]> = {}

  for (const [sid, list] of Object.entries(map)) {
    const kept = list.filter(item => {
      if (isStaleSubagent(item, now)) {
        dropped += 1
        return false
      }

      return true
    })

    next[sid] = kept
  }

  if (dropped > 0) {
    $subagentsBySession.set(next)
  }

  scheduleStaleSweep()

  return dropped
}

/** Reconnect reconciliation: drop every non-terminal row in every session.
 *  Called from the primary reconnect path (use-gateway-boot) and the scoped
 *  secondary reconnect path (gateway.ts), next to reconcileBusyStatesOnReconnect.
 *  The re-dialed backend re-mints runtime ids, so pre-drop rows can never
 *  receive their terminal events; genuinely live work re-creates its row via
 *  upsertSubagent(createIfMissing) on its next event. */
export function dropUnfinishedSubagentsOnReconnect(): number {
  if (staleTimer !== null) {
    clearTimeout(staleTimer)
    staleTimer = null
  }

  const map = $subagentsBySession.get()
  let dropped = 0
  const next: Record<string, SubagentProgress[]> = {}

  for (const [sid, list] of Object.entries(map)) {
    const kept = list.filter(item => {
      if (!TERMINAL.has(item.status)) {
        dropped += 1
        return false
      }

      return true
    })

    next[sid] = kept
  }

  if (dropped > 0) {
    $subagentsBySession.set(next)
  }

  return dropped
}

export function buildSubagentTree(items: readonly SubagentProgress[]): SubagentNode[] {
  const nodes = new Map<string, SubagentNode>()

  for (const item of items) {
    nodes.set(item.id, { ...item, children: [] })
  }

  const roots: SubagentNode[] = []

  for (const node of nodes.values()) {
    const parent = node.parentId ? nodes.get(node.parentId) : null

    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }

  const sort = (a: SubagentNode, b: SubagentNode) =>
    a.startedAt - b.startedAt || a.taskIndex - b.taskIndex || a.goal.localeCompare(b.goal)

  const walk = (node: SubagentNode) => node.children.sort(sort).forEach(walk)
  roots.sort(sort).forEach(walk)

  return roots
}

export const activeSubagentCount = (items: readonly SubagentProgress[], now = Date.now()) =>
  items.filter(item => (item.status === 'queued' || item.status === 'running') && !isStaleSubagent(item, now)).length

export const failedSubagentCount = (items: readonly SubagentProgress[]) =>
  items.filter(item => item.status === 'failed' || item.status === 'interrupted').length

/** Flatten every session's subagents — the scope the Spawn-tree panel and the
 *  status-bar indicator must agree on. */
export const allSubagents = (bySession: Record<string, SubagentProgress[]>) => Object.values(bySession).flat()
