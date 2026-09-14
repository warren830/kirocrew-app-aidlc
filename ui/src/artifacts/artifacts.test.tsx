/**
 * What the artifacts area must not get wrong.
 *
 * Every case here is a claim the UI makes about evidence, and each one is a way the tool could lie:
 * rendering an artifact through something other than the host sanitiser, "rendering" a JSON file as
 * markdown, fetching a file the backend already said is too large, showing a diff for a file with no
 * prior version, offering an anchor the reviewer never recorded, or offering an editor hand-off the host
 * does not have.
 */

import type { ReactElement } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import type { ArtifactMeta, ReviewFinding } from '../lib/types'
import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import { ArtifactsTab } from './ArtifactsTab'
import { EvidenceDrawer } from './EvidenceDrawer'
import { FindingList } from './FindingList'
import { ReviewTab } from './ReviewTab'
import { classifyDiffLine, parseDiff } from './ArtifactDiff'
import { headingSlug } from './ArtifactViewer'

const BASE = '/api/apps/aidlc-studio'
const REPO = 'r_1'
const INTENT = 'r_1/default/guest-checkout'

const meta = (over: Partial<ArtifactMeta> = {}): ArtifactMeta => ({
  artifact_id: 'a1',
  relpath: '.aidlc/spaces/default/guest-checkout/functional-design.md',
  name: 'functional-design.md',
  stage: 'functional-design',
  phase: 'inception',
  unit: null,
  size: 2048,
  mtime: '2026-09-04T10:00:00Z',
  sha256: 'ab'.repeat(32),
  kind: 'artifact',
  renderable: true,
  ...over,
})

const finding = (over: Partial<ReviewFinding> = {}): ReviewFinding => ({
  level: 'blocker',
  title: 'Guest checkout does not define a decline path',
  quote: 'no behaviour is specified when the gateway declines',
  anchor: null,
  reviewer: 'architecture-reviewer',
  iteration: 2,
  ...over,
})

function installArtifact(body: Record<string, unknown>, artifacts: ArtifactMeta[] = [meta()]) {
  // One wildcard route for both shapes: `/artifacts` (the listing) and `/artifacts/<id>` (one body),
  // which is also how the real routes are shaped.
  setApiRoutes({
    [`GET ${BASE}/repos/${REPO}/intents/${encodeURIComponent(INTENT)}/artifacts*`]: (_b, path) =>
      path.includes('/artifacts/')
        ? {
            artifact: artifacts[0],
            content: null,
            encoding: 'utf-8',
            truncated: false,
            review: null,
            toc: [],
            prior: { available: false, source: null, diff: null },
            ...body,
          }
        : { artifacts, truncated: false, count: artifacts.length },
  })
}

const mount = (ui: ReactElement) => render(<I18nProvider>{ui}</I18nProvider>)

describe('the artifact viewer', () => {
  it('renders markdown through the host renderer inside a .msg-content wrapper', async () => {
    installArtifact({ content: '# Functional design\n\nGuest checkout.' })
    const { container } = mount(
      <ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta()]} />,
    )

    const markdown = await screen.findByTestId('markdown')
    expect(markdown).toHaveTextContent('Guest checkout.')
    // The host's prose styles are keyed on this ancestor class; without it the artifact is unstyled.
    expect(markdown.closest('.msg-content')).not.toBeNull()
    expect(container.querySelector('.studio-plain')).toBeNull()
  })

  it('shows a file that is not markdown as text rather than pretending to render it', async () => {
    const json = meta({ artifact_id: 'a2', relpath: 'x/traceability.json', name: 'traceability.json', kind: 'traceability' })
    installArtifact({ artifact: json, content: '{"a": 1, "b": "# not a heading"}' }, [json])
    const { container } = mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[json]} />)

    await waitFor(() => expect(container.querySelector('.studio-plain')).not.toBeNull())
    expect(container.querySelector('.studio-plain')).toHaveTextContent('# not a heading')
    expect(screen.queryByTestId('markdown')).toBeNull()
  })

  it('never reads an artifact the backend already marked unrenderable, and says why', async () => {
    const big = meta({ artifact_id: 'a3', size: 8 * 1024 * 1024, renderable: false })
    installArtifact({}, [big])
    mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[big]} />)

    expect(await screen.findByText(/did not render this file/i)).toBeInTheDocument()
    // The size appears twice by design: once as file metadata, once inside the explanation.
    expect(screen.getAllByText(/8 MB/).length).toBeGreaterThan(0)
    expect(apiCalls.some((call) => call.path.includes('/artifacts/a3'))).toBe(false)
  })

  it('offers no editor hand-off unless the host reports that capability', async () => {
    installArtifact({ content: '# design' })
    const open = vi.fn()
    const { rerender } = mount(
      <ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta()]} onOpenInEditor={open} />,
    )
    await screen.findByTestId('markdown')
    expect(screen.queryByRole('button', { name: /open in editor/i })).toBeNull()

    rerender(
      <I18nProvider>
        <ArtifactsTab
          repoId={REPO}
          intentKey={INTENT}
          artifacts={[meta()]}
          onOpenInEditor={open}
          capabilities={{ open_in_editor: { available: true, reason: null } }}
        />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: /open in editor/i }))
    expect(open).toHaveBeenCalledWith(meta().relpath)
  })

  it('reveals the heading a table-of-contents entry names', async () => {
    installArtifact({
      content: '# One\n\n## Decline path\n',
      toc: [
        { level: 1, text: 'One', anchor: 'one' },
        { level: 2, text: 'Decline path', anchor: 'decline-path' },
      ],
    })
    // The stubbed MarkdownRenderer emits a <pre>, so the heading the anchor targets is created here to
    // stand in for the host's rendered output; the lookup under test is slug matching, not markdown.
    const { container } = mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta()]} />)
    await screen.findByTestId('markdown')
    const body = container.querySelector('.studio-pane-body') as HTMLElement
    const heading = document.createElement('h2')
    heading.textContent = 'Decline path'
    body.appendChild(heading)

    await userEvent.click(screen.getByRole('button', { name: 'Decline path' }))
    expect(heading.classList.contains('studio-hl')).toBe(true)
  })

  it('matches the backend heading-anchor rule', () => {
    expect(headingSlug('Decline path')).toBe('decline-path')
    expect(headingSlug('3.4 Code generation!')).toBe('34-code-generation')
  })
})

describe('the diff', () => {
  it('classifies unified-diff lines and keeps the sign in the text', () => {
    expect(classifyDiffLine('@@ -1,3 +1,4 @@')).toBe('hunk')
    expect(classifyDiffLine('+++ b/x.md')).toBe('meta')
    expect(classifyDiffLine('+added')).toBe('add')
    expect(classifyDiffLine('-gone')).toBe('del')
    expect(classifyDiffLine(' same')).toBe('context')
    expect(parseDiff('+a\n-b\n')).toHaveLength(2)
  })

  it('appears only when the backend supplied a prior version', async () => {
    installArtifact({ content: '# design', prior: { available: false, source: null, diff: '+nope' } })
    const { container, rerender } = mount(
      <ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta()]} />,
    )
    await screen.findByTestId('markdown')
    expect(container.querySelector('.studio-diff')).toBeNull()

    installArtifact({
      content: '# design',
      prior: { available: true, source: 'git', diff: '@@ -1 +1 @@\n-old line\n+new line\n' },
    })
    rerender(
      <I18nProvider>
        <ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta({ artifact_id: 'a1b' })]} />
      </I18nProvider>,
    )
    await waitFor(() => expect(container.querySelector('.studio-diff')).not.toBeNull())
    const lines = container.querySelectorAll('.studio-diffline')
    expect([...lines].map((line) => line.getAttribute('data-kind'))).toEqual(['hunk', 'del', 'add'])
    expect(container.querySelector('[data-kind="add"]')).toHaveTextContent('+new line')
    expect(screen.getByText(/prior version derived from Git/i)).toBeInTheDocument()
  })
})

describe('reviewer findings', () => {
  it('links into the artifact only when the backend recorded an anchor, and says so when it did not', async () => {
    const open = vi.fn()
    mount(
      <FindingList
        findings={[finding(), finding({ level: 'advisory', anchor: 'decline-path', title: 'Add a retry note' })]}
        onOpenAnchor={open}
      />,
    )

    const items = screen.getAllByRole('article')
    expect(within(items[0] as HTMLElement).queryByRole('button', { name: /in artifact/i })).toBeNull()
    expect(within(items[0] as HTMLElement).getByText(/recorded no section/i)).toBeInTheDocument()

    await userEvent.click(within(items[1] as HTMLElement).getByRole('button', { name: /in artifact/i }))
    expect(open).toHaveBeenCalledWith('decline-path')
  })

  it('keeps the reviewer text verbatim and gives every finding the route id f-<n>', () => {
    const { container } = mount(<FindingList findings={[finding(), finding({ level: 'resolved' })]} />)
    expect(screen.getAllByText(finding().title)).toHaveLength(2)
    expect(container.querySelector('#f-1')).not.toBeNull()
    expect(container.querySelector('#f-2')?.getAttribute('data-level')).toBe('resolved')
  })

  it('shows findings the reviewer did not classify instead of dropping them', () => {
    mount(<FindingList findings={[finding({ level: 'unknown' })]} grouped />)
    expect(screen.getByText(/Level not stated by the reviewer — 1/)).toBeInTheDocument()
  })
})

describe('the review tab', () => {
  const installReview = (over: Record<string, unknown> = {}) =>
    setApiRoutes({
      [`GET ${BASE}/repos/${REPO}/intents/${encodeURIComponent(INTENT)}/review`]: () => ({
        stage: 'functional-design',
        verdict: 'CHANGES_REQUESTED',
        findings: [finding(), finding({ level: 'advisory', title: 'Name the retry budget' })],
        receipts: [
          {
            event: 'REVIEW_COMPLETED',
            ts: '2026-09-04T09:41:12Z',
            reviewer: 'architecture-reviewer',
            iteration: 2,
            verdict: 'CHANGES_REQUESTED',
            fingerprint: null,
          },
        ],
        review_class: 'architecture',
        reviewer: 'architecture-reviewer',
        revisions: 1,
        ...over,
      }),
    })

  it('groups findings by level with counts, and keeps AI-DLC words verbatim', async () => {
    installReview()
    mount(<ReviewTab repoId={REPO} intentKey={INTENT} />)

    expect(await screen.findByText(/Open blockers — 1/)).toBeInTheDocument()
    expect(screen.getByText(/Advisory — 1/)).toBeInTheDocument()
    // Twice: the verdict chip and the audit receipt row, both verbatim.
    expect(screen.getAllByText('CHANGES_REQUESTED')).toHaveLength(2)
    expect(screen.getByText('REVIEW_COMPLETED')).toBeInTheDocument()
    expect(screen.getByText('architecture')).toBeInTheDocument()
  })

  it('prefers the card evidence over the live file, so a decision is shown its own findings', async () => {
    installReview()
    mount(
      <ReviewTab
        repoId={REPO}
        intentKey={INTENT}
        review={{
          verdict: 'APPROVED',
          findings: [finding({ level: 'resolved', title: 'Captured finding' })],
          reviewer: 'architecture-reviewer',
          review_class: 'architecture',
          iteration: 1,
        }}
      />,
    )

    expect(await screen.findByText('Captured finding')).toBeInTheDocument()
    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.queryByText(/Name the retry budget/)).toBeNull()
  })

  it('explains an empty review instead of showing an empty pane', async () => {
    installReview({ verdict: null, findings: [], receipts: [] })
    mount(<ReviewTab repoId={REPO} intentKey={INTENT} />)
    expect(await screen.findByText(/No reviewer findings for this stage/)).toBeInTheDocument()
  })
})

describe('the evidence drawer', () => {
  const sections = [
    {
      id: 'audit',
      title: 'Audit shard',
      source: 'aidlc_audit' as const,
      ref: '.aidlc/audit/audit-1.md',
      raw: '**Event**: GATE_OPENED\n**Stage**: functional-design',
      facts: [{ label: 'Size', value: '4 KB', mono: true }],
    },
    { id: 'session', title: 'Session', source: 'kirocrew_session' as const, raw: null, conflict: true },
  ]

  it('reports that it was opened once, shows raw text verbatim, and closes on Escape', async () => {
    const opened = vi.fn()
    const close = vi.fn()
    const { container } = mount(
      <EvidenceDrawer open onClose={close} sections={sections} onOpened={opened} />,
    )

    expect(opened).toHaveBeenCalledTimes(1)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    // Raw evidence is text in a <pre>, never markdown: re-interpreting an audit block could change
    // what the user believes it said.
    const pre = container.querySelector('pre')
    expect(pre).toHaveTextContent('**Event**: GATE_OPENED')
    expect(screen.queryByTestId('markdown')).toBeNull()
    expect(screen.getByText(/conflicts with another source/i)).toBeInTheDocument()
    expect(screen.getByText(/This source had nothing recorded/i)).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('renders nothing at all when closed, so it cannot satisfy the "evidence was seen" gate', () => {
    const opened = vi.fn()
    const { container } = mount(
      <EvidenceDrawer open={false} onClose={() => {}} sections={sections} onOpened={opened} />,
    )
    expect(container.querySelector('.studio-drawer')).toBeNull()
    expect(opened).not.toHaveBeenCalled()
  })
})

describe('the artifacts tab', () => {
  it('reads the listing itself when no metadata was handed in, and lists files by stage', async () => {
    const files = [
      meta(),
      meta({ artifact_id: 'a2', name: 'nfr-requirements-questions.md', stage: 'nfr-requirements', kind: 'questions' }),
    ]
    installArtifact({ content: '# design' }, files)
    mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} />)

    expect(await screen.findByText('2 files')).toBeInTheDocument()
    expect(screen.getByText('nfr-requirements')).toBeInTheDocument()
    expect(apiCalls.some((call) => call.path.endsWith('/artifacts'))).toBe(true)
  })

  it('explains an empty record instead of rendering a blank tab', async () => {
    installArtifact({}, [])
    mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} />)
    expect(await screen.findByText(/No artifacts recorded/i)).toBeInTheDocument()
  })

  it('says when a deep-linked artifact is not in this record', async () => {
    installArtifact({ content: '# design' })
    mount(<ArtifactsTab repoId={REPO} intentKey={INTENT} artifacts={[meta()]} artifactId="gone" />)
    expect(await screen.findByText(/not in this record/i)).toBeInTheDocument()
  })
})
