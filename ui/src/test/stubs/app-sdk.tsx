/**
 * Test stand-in for `@kirocrew/app-sdk`.
 *
 * The real module is provided by the dashboard at runtime through its import map; there is no npm
 * package, so a test that imports a component would fail to resolve it. Vitest aliases the specifier
 * here (see `vitest.config.ts`).
 *
 * The stub is deliberately behavioural, not a spy soup: `useAppApi` dispatches to a route table a test
 * installs with `setApiRoutes`, so component tests exercise the real request paths and the real error
 * decoding instead of asserting that a mock was called.
 */
import { createContext, useContext, type ReactNode } from 'react'

export interface AppApi {
  get<T = unknown>(path: string, init?: RequestInit): Promise<T>
  post<T = unknown>(path: string, body?: unknown): Promise<T>
  put<T = unknown>(path: string, body?: unknown): Promise<T>
  patch<T = unknown>(path: string, body?: unknown): Promise<T>
  del<T = unknown>(path: string): Promise<T>
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type RouteHandler = (body: unknown, path: string) => unknown | Promise<unknown>

const routes = new Map<string, RouteHandler>()
export const apiCalls: { method: Method; path: string; body: unknown }[] = []

/** Install handlers keyed `"GET /api/apps/aidlc-studio/actions"`. A `*` suffix matches a prefix. */
export function setApiRoutes(table: Record<string, RouteHandler>): void {
  routes.clear()
  apiCalls.length = 0
  for (const [key, handler] of Object.entries(table)) routes.set(key, handler)
}

export class StubApiError extends Error {
  constructor(readonly status: number, readonly body: unknown) {
    super(`API ${status}: ${JSON.stringify(body)}`)
  }
}

async function dispatch(method: Method, path: string, body?: unknown): Promise<unknown> {
  apiCalls.push({ method, path, body })
  const exact = routes.get(`${method} ${path}`)
  if (exact) return exact(body, path)
  for (const [key, handler] of routes) {
    const [m, pattern] = key.split(' ')
    if (m !== method || !pattern?.endsWith('*')) continue
    if (path.startsWith(pattern.slice(0, -1))) return handler(body, path)
  }
  throw new StubApiError(404, { error: 'no stub route', code: 'route_not_found', details: { path } })
}

const api: AppApi = {
  get: (path, _init) => dispatch('GET', path) as Promise<never>,
  post: (path, body) => dispatch('POST', path, body) as Promise<never>,
  put: (path, body) => dispatch('PUT', path, body) as Promise<never>,
  patch: (path, body) => dispatch('PATCH', path, body) as Promise<never>,
  del: (path) => dispatch('DELETE', path) as Promise<never>,
}

export function useAppApi(): AppApi {
  return api
}

export const navigations: string[] = []
export function useNavigate(): (path: string) => void {
  return (path) => {
    navigations.push(path)
    window.history.pushState(null, '', path)
  }
}

export const badges: number[] = []
export function useNavBadge(): (count: number) => void {
  return (count) => void badges.push(count)
}

export const notifications: { message: string; type?: string }[] = []
export function useNotify() {
  return (message: string, opts?: { type?: 'info' | 'success' | 'error' }) =>
    void notifications.push({ message, type: opts?.type })
}

export function useAppInfo() {
  return {
    name: 'aidlc-studio',
    version: '1.0.0',
    permissions: { api: ['/api/apps/aidlc-studio', '/api/chat'], events: [] },
  }
}

export function useTheme() {
  return { mode: 'dark' as const, accent: '#00d492', colorTheme: 'default' }
}

export function useAppEvents(_event: string, _cb: (data: unknown) => void): void {
  // The real host never dispatches these; a stub that fired would let a test pass on a path that
  // cannot work in production.
}

export const chatLaunches: { agent?: string; message?: string }[] = []
export function useChatLauncher() {
  return { openChat: (opts?: { agent?: string; message?: string }) => void chatLaunches.push(opts ?? {}) }
}

export function useChatSession() {
  return {
    status: 'no-session' as const,
    slotKey: null,
    slotInfo: null,
    creating: false,
    error: null,
    openChat: () => {},
    createSession: async () => {},
    resetSession: () => {},
  }
}

const AppApiContext = createContext<AppApi | null>(null)
export function AppApiProvider({ children }: { children: ReactNode }) {
  return <AppApiContext.Provider value={api}>{children}</AppApiContext.Provider>
}
export function useAppApiContext(): AppApi | null {
  return useContext(AppApiContext)
}

export const ChatEmbed = ({ slotKey }: { slotKey: string }) => <div data-testid="chat-embed">{slotKey}</div>
export const ChatPanel = ChatEmbed
export const ChatMessageList = ({ messages }: { messages: unknown[] }) => (
  <div data-testid="chat-messages">{messages.length}</div>
)
export const defaultMessageRenderers: readonly unknown[] = []
export const GROUPED_ROLES: readonly string[] = ['thinking', 'permission']
export const ToolCallPill = () => null
export function mergeRenderers(extra: readonly unknown[]) {
  return extra
}
export function resolveRenderer() {
  return undefined
}
export function parseOptions(content: string) {
  return { text: content, options: [] as string[], multi: false, isPlan: false }
}
export function deriveFollowUpOptions() {
  return { followUpOptions: [] as string[], followUpIsPlan: false }
}
export function extractSteeringAcks(content: string) {
  return { cleaned: content, acks: [] as string[] }
}
export function stripPartialOptionMarker(text: string) {
  return text
}
export function checkSubscribeAllowed() {
  return { level: 'ok' as const }
}
