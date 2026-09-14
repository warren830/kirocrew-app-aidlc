/**
 * Test stand-in for `lucide-react`.
 *
 * The real module is a large icon set the host provides. Tests only need every icon name to resolve to
 * something renderable and inert, so the default export is a Proxy that mints an SVG-less span per name
 * — matching how the app consumes it (`import lucide from 'lucide-react'` then destructure), which is
 * the only form that works against the host's vendor stub.
 */
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number | string }

function makeIcon(name: string) {
  const Icon = ({ size = 16, ...rest }: IconProps) => (
    <span data-icon={name} data-size={String(size)} aria-hidden={rest['aria-hidden'] ?? true} />
  )
  Icon.displayName = name
  return Icon
}

const cache = new Map<string, ReturnType<typeof makeIcon>>()
const handler: ProxyHandler<Record<string, unknown>> = {
  get: (_target, prop: string) => {
    if (prop === '__esModule') return true
    if (!cache.has(prop)) cache.set(prop, makeIcon(prop))
    return cache.get(prop)
  },
}

const proxy = new Proxy({}, handler) as Record<string, ReturnType<typeof makeIcon>>
export default proxy
export const AlertTriangle = proxy.AlertTriangle
export const Check = proxy.Check
export const ChevronRight = proxy.ChevronRight
export const Clock = proxy.Clock
export const Inbox = proxy.Inbox
export const Loader2 = proxy.Loader2
export const Package = proxy.Package
export const RefreshCw = proxy.RefreshCw
export const Settings = proxy.Settings
export const Shield = proxy.Shield
