/**
 * The dependency edges, drawn only for the selected stage and only in the `dependencies` density.
 *
 * Why this exists at all: the mockup renders `dependencies` identically to `detailed` and draws no
 * edges (visual spec §6.1, discrepancy #13), but FR-MAP-004/005/007 want three distinct densities and
 * an overlay of the selected stage's relationships. The resolution in the register is "draw edges for
 * selection in `dependencies`", which is what this does — and only for the selection, so FR-MAP-004
 * ("the default view does not draw every dependency edge") still holds in every mode.
 *
 * Two properties keep it safe:
 *
 *  - **It is decoration, and says so.** `aria-hidden` plus `pointer-events:none`. Every edge drawn here
 *    is also written out as text in the inspector's Relationships block and tagged on the cards
 *    themselves, so a screen-reader user loses nothing and no meaning is carried by a line alone.
 *  - **It measures, it does not assume.** Cards wrap, so an edge's endpoints cannot be derived from the
 *    data; they are read from the DOM after layout and re-read on resize. In an environment with no
 *    layout (jsdom, a hidden pane) every rect is zero and the paths collapse harmlessly.
 */

import { useCallback, useEffect, useLayoutEffect, useState } from 'react'

import type { Relation } from './StageCard'

export interface DependencyOverlayProps {
  /** The positioned element the cards live in; the SVG is absolutely placed over it. */
  canvas: HTMLElement | null
  from: string
  relations: Map<string, Exclude<Relation, null>>
  /**
   * Changes whenever layout could have moved: density, unit expansion, selection, data. Re-measuring on
   * every render would fight React; re-measuring never would leave edges pointing at old positions.
   */
  token: string
}

interface Edge {
  slug: string
  relation: Exclude<Relation, null>
  d: string
  x: number
  y: number
}

function centre(rect: DOMRect, origin: DOMRect): { x: number; y: number } {
  return { x: rect.left - origin.left + rect.width / 2, y: rect.top - origin.top + rect.height / 2 }
}

export function DependencyOverlay({ canvas, from, relations, token }: DependencyOverlayProps) {
  const [edges, setEdges] = useState<Edge[]>([])

  const measure = useCallback(() => {
    if (!canvas || !from || relations.size === 0) {
      setEdges([])
      return
    }
    const origin = canvas.getBoundingClientRect()
    // One pass over the cards, keyed by slug: building a selector per slug would need `CSS.escape`
    // (absent in some test environments) and would query the tree once per relation.
    const cards = new Map<string, HTMLElement>()
    for (const node of canvas.querySelectorAll<HTMLElement>('.studio-stage[data-variant="stage"]')) {
      const slug = node.dataset['stage']
      if (slug && !cards.has(slug)) cards.set(slug, node)
    }
    const cardOf = (slug: string): HTMLElement | null => cards.get(slug) ?? null

    const source = cardOf(from)
    if (!source) {
      setEdges([])
      return
    }
    const a = centre(source.getBoundingClientRect(), origin)
    const next: Edge[] = []
    for (const [slug, relation] of relations) {
      const target = cardOf(slug)
      if (!target) continue
      const b = centre(target.getBoundingClientRect(), origin)
      // A quadratic bow so two edges between neighbouring lanes stay distinguishable; the control point
      // leans perpendicular to the segment rather than straight up, so vertical and horizontal pairs
      // both curve.
      const cx = (a.x + b.x) / 2 + (a.y - b.y) * 0.12
      const cy = (a.y + b.y) / 2 + (b.x - a.x) * 0.12
      next.push({ slug, relation, d: `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`, x: b.x, y: b.y })
    }
    setEdges(next)
  }, [canvas, from, relations])

  useLayoutEffect(measure, [measure, token])

  useEffect(() => {
    if (!canvas) return
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(() => measure())
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [canvas, measure])

  if (edges.length === 0) return null

  return (
    <svg className="studio-map-edges" aria-hidden focusable="false">
      {edges.map((edge) => (
        <g key={`${edge.relation}:${edge.slug}`} className="studio-map-edge" data-relation={edge.relation} data-to={edge.slug}>
          <path d={edge.d} fill="none" vectorEffect="non-scaling-stroke" />
          <circle cx={edge.x} cy={edge.y} r={2.5} />
        </g>
      ))}
    </svg>
  )
}
