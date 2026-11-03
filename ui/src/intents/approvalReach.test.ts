import { describe, expect, it } from 'vitest'

import type { RepoRecord } from '../lib/types'
import { approvalCommand, approvalPurpose, approvalReach } from './approvalReach'

const REPOS = [
  { repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' },
  { repo_id: 'r_v2', label: 'v2-feature', canonical_path: '/work/v2-feature' },
  { repo_id: 'r_in', label: 'nested', canonical_path: '/work/v2-feature/packages/nested' },
] as RepoRecord[]

describe('approvalCommand', () => {
  it('reads the command out of the JSON arguments, whole or cut off', () => {
    const whole = JSON.stringify({ __tool_use_purpose: 'why', command: 'grep -n "a\\b" x.md' })
    expect(approvalCommand(whole)).toBe('grep -n "a\\b" x.md')
    expect(approvalCommand(whole.slice(0, whole.length - 6))).toBe('grep -n "a\\b" ')
    expect(approvalCommand(JSON.stringify({ file_path: '/work/local-s3/README.md' }))).toBe('/work/local-s3/README.md')
  })

  it('keeps an input that is not JSON as the host gave it', () => {
    expect(approvalCommand('go test ./...')).toBe('go test ./...')
  })
})

describe('approvalPurpose', () => {
  it("prefers the host's field, and falls back to the one inside the arguments", () => {
    const input = JSON.stringify({ __tool_use_purpose: 'from the arguments', command: 'ls' })
    expect(approvalPurpose('from the host', input)).toBe('from the host')
    expect(approvalPurpose('', input)).toBe('from the arguments')
    expect(approvalPurpose(undefined, 'ls')).toBe('')
  })
})

describe('approvalReach', () => {
  it('names another registered repository, the deepest root first, and nothing for its own paths', () => {
    const reach = approvalReach(
      'grep -rn x /work/v2-feature/aidlc/audit /work/v2-feature/packages/nested/a.md /work/local-s3/aidlc', 'r_s3', REPOS)
    expect(reach.otherRepos).toEqual([
      { label: 'v2-feature', path: '/work/v2-feature' }, { label: 'nested', path: '/work/v2-feature/packages/nested' }])
    expect(reach.outside).toEqual([])
  })

  it('lists other absolute paths, and ignores device files and lookalike roots', () => {
    const reach = approvalReach('cat /tmp/x.log > /dev/null; ls /work/local-s3-old/', 'r_s3', REPOS)
    expect(reach.otherRepos).toEqual([])
    expect(reach.outside).toEqual(['/tmp/x.log', '/work/local-s3-old/'])
  })

  it('finds nothing in a command that stays in the repository', () => {
    expect(approvalReach('bun .kiro/tools/aidlc.ts engine now', 'r_s3', REPOS))
      .toEqual({ otherRepos: [], outside: [] })
  })
})
