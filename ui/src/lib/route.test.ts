/**
 * The route contract (§3.2).
 *
 * Two properties matter beyond "it parses": every value is validated before it can become a path
 * segment in an API call, and `parseRoute(buildRoute(r))` is `r` — a deep link from Slack has to
 * restore exactly the scope, selection, tab and anchor it named (PRD §10.2, FR-SLK-008).
 */

import { describe, expect, it } from 'vitest'

import { buildRoute, EMPTY_ROUTE, PAGE_PATH, paneFor, parseRoute, type StudioRoute } from './route'

const parse = (url: string): StudioRoute => {
  const [search = '', hash = ''] = url.split('#')
  return parseRoute(search.replace(/^[^?]*/, ''), hash ? `#${hash}` : '')
}

describe('parseRoute', () => {
  it('defaults to the Action Center', () => {
    expect(parse('')).toEqual(EMPTY_ROUTE)
    expect(parse('?').view).toBe('actions')
  })

  it('accepts every known view and rejects anything else', () => {
    expect(parse('?view=map').view).toBe('map')
    expect(parse('?view=new-intent').view).toBe('new-intent')
    expect(parse('?view=admin').view).toBe('actions')
    expect(parse('?view=').view).toBe('actions')
  })

  it('forces the Action Center when a deep link names an action but no view', () => {
    const route = parse('?action=a_7712')
    expect(route.view).toBe('actions')
    expect(route.action).toBe('a_7712')
  })

  it('derives the default space only when an intent is named', () => {
    expect(parse('?intent=250901-guest-checkout').space).toBe('default')
    expect(parse('?repo=r_1').space).toBe('')
    expect(parse('?intent=team-b~250901-guest-checkout')).toMatchObject({
      space: 'default',
      intent: 'team-b~250901-guest-checkout',
    })
    expect(parse('?space=team-b&intent=team-b~250901-guest-checkout').space).toBe('team-b')
  })

  it('drops values that could change a URL or a path segment', () => {
    // Each of these would otherwise be interpolated into an API path.
    expect(parse('?repo=../../health').repo).toBe('')
    expect(parse('?repo=r%2F1').repo).toBe('')
    expect(parse('?action=a 1').action).toBe('')
    expect(parse(`?stage=${'x'.repeat(161)}`).stage).toBe('')
    expect(parse('?artifact=%00').artifact).toBe('')
    // And the legitimate spellings survive.
    expect(parse('?repo=r_ab12&stage=functional-design&unit=unit-1')).toMatchObject({
      repo: 'r_ab12',
      stage: 'functional-design',
      unit: 'unit-1',
    })
  })

  it('validates the tab and the evidence anchor', () => {
    expect(parse('?tab=review').tab).toBe('review')
    expect(parse('?tab=nope').tab).toBe('decision')
    expect(parse('#f-3').anchor).toBe('f-3')
    expect(parse('#crit-12').anchor).toBe('crit-12')
    expect(parse('#h-acceptance-criteria').anchor).toBe('h-acceptance-criteria')
    expect(parse('#<script>').anchor).toBe('')
  })
})

describe('buildRoute', () => {
  it('omits defaults and empties', () => {
    expect(buildRoute({})).toBe(PAGE_PATH)
    expect(buildRoute({ view: 'actions' })).toBe(PAGE_PATH)
    expect(buildRoute({ view: 'repos' })).toBe(`${PAGE_PATH}?view=repos`)
    expect(buildRoute({ view: 'actions', action: 'a_1', tab: 'decision' })).toBe(
      `${PAGE_PATH}?view=actions&action=a_1`,
    )
  })

  it('is exactly the deep link the backend hands to Slack and notifications', () => {
    // `ActionCard.deep_link` is `/apps/aidlc-studio?view=actions&action=<id>` (`C.DEEP_LINK_BASE`).
    expect(buildRoute({ view: 'actions', action: 'a_7712' })).toBe(
      '/apps/aidlc-studio?view=actions&action=a_7712',
    )
  })

  it('writes the params in the contract order', () => {
    const url = buildRoute({
      view: 'map',
      repo: 'r_1',
      space: 'team-b',
      intent: 'team-b~250901-x',
      action: 'a_1',
      tab: 'review',
      stage: 'functional-design',
      unit: 'unit-1',
      artifact: 'art_1',
      tx: 'tx_1',
      draft: 'd_1',
      anchor: 'f-2',
    })
    expect(url).toBe(
      `${PAGE_PATH}?view=map&repo=r_1&space=team-b&intent=team-b~250901-x&action=a_1&tab=review` +
        '&stage=functional-design&unit=unit-1&artifact=art_1&tx=tx_1&draft=d_1#f-2',
    )
  })

  it('merges a patch onto a base instead of replacing it', () => {
    const base = parse('?view=actions&action=a_1&tab=decision&repo=r_1')
    expect(buildRoute({ tab: 'review' }, base)).toBe(`${PAGE_PATH}?view=actions&repo=r_1&action=a_1&tab=review`)
    // Clearing is explicit, and clears only what was named.
    expect(buildRoute({ action: '' }, base)).toBe(`${PAGE_PATH}?repo=r_1`)
  })

  it('refuses to write a value it would not parse back', () => {
    expect(buildRoute({ repo: '../evil' })).toBe(PAGE_PATH)
    expect(buildRoute({ anchor: 'not an anchor' })).toBe(PAGE_PATH)
  })

  it('round-trips every valid route', () => {
    const cases: StudioRoute[] = [
      { ...EMPTY_ROUTE },
      { ...EMPTY_ROUTE, view: 'repos', repo: 'r_ab12' },
      { ...EMPTY_ROUTE, view: 'actions', action: 'a_7712', tab: 'artifacts', artifact: 'art_9' },
      {
        ...EMPTY_ROUTE,
        view: 'map',
        repo: 'r_1',
        space: 'default',
        intent: '250901-guest-checkout',
        stage: 'functional-design',
        unit: 'unit-1',
        anchor: 'h-review',
      },
      { ...EMPTY_ROUTE, view: 'repos', repo: 'r_1', tx: 'tx_abc' },
      { ...EMPTY_ROUTE, view: 'actions', action: 'a_1', draft: 'd_1', anchor: 'crit-3' },
      { ...EMPTY_ROUTE, view: 'intents', space: 'team-b' },
    ]
    for (const route of cases) {
      expect(parse(buildRoute(route))).toEqual(route)
    }
  })
})

describe('paneFor', () => {
  it('derives the mobile pane from the selection, with no extra param', () => {
    expect(paneFor(EMPTY_ROUTE)).toBe('list')
    expect(paneFor({ ...EMPTY_ROUTE, action: 'a_1' })).toBe('detail')
    expect(paneFor({ ...EMPTY_ROUTE, view: 'repos', repo: 'r_1' })).toBe('list')
  })
})
