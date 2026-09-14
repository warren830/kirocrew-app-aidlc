import { describe, expect, it } from 'vitest'
import { actionCard } from '../test/fixtures'
import type { ActionsResponse } from './types'
import { forActionQueue } from './actionQueue'

describe('archived repository actions', () => {
  it('hides them from the waiting list and counts while preserving the raw history', () => {
    const live = actionCard()
    const archived = { ...actionCard(), action_id: 'archived', repo: { ...live.repo, archived: true } }
    const response = {
      actions: [live, archived], organize: 'priority', groups: [],
      counts: { total: 2, critical: 0, blocking: 2, attention: 0, info: 0 }, generated_at: '',
    } as ActionsResponse
    const visible = forActionQueue(response)
    expect(visible.actions).toEqual([live])
    expect(visible.counts.total).toBe(1)
    expect(visible.counts.blocking).toBe(1)
    expect(response.actions).toHaveLength(2)
  })
})
