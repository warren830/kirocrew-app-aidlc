/**
 * The one class component in Studio.
 *
 * React 18 has no hook or function-component API for catching a render error: `componentDidCatch` and
 * `getDerivedStateFromError` exist only on a class. The alternative is no boundary at all, and a throw
 * inside one view would then unmount the whole app page — the user would see a blank frame with no way
 * back, which is exactly the failure this file exists to prevent. `Component` is in the host's react
 * vendor stub, so importing it is safe (`03 §1.3`).
 *
 * What it renders matters as much as that it renders: the failing view's name, the error's own
 * message, and an explicit statement that nothing was sent. A crash in a decision UI must never leave
 * the user wondering whether their approval went out.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

import { makeI18n, type I18n } from '../i18n'
import { readLocale } from '../lib/host'
import { Icon } from './Icon'

export interface ErrorBoundaryProps {
  /** What failed, already translated — the view label, so the message names something the user sees. */
  where: string
  /** Cleared with the retry; changing it also resets the boundary (a new view gets a fresh chance). */
  resetKey?: string
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
  resetKey?: string
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { error: null, resetKey: props.resetKey }
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error }
  }

  static getDerivedStateFromProps(
    props: ErrorBoundaryProps,
    state: ErrorBoundaryState,
  ): Partial<ErrorBoundaryState> | null {
    // Navigating away from a broken view must not keep showing its error.
    if (props.resetKey !== state.resetKey) return { error: null, resetKey: props.resetKey }
    return null
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // The console is the only sink available: `useNotify` has no listener in the shipped dashboard and
    // Studio must not post a UI crash to its own backend (it would be a write from a broken state).
    console.error('[aidlc-studio] render failed in %s', this.props.where, error, info.componentStack)
  }

  private readonly retry = (): void => {
    this.setState({ error: null })
  }

  override render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    // The boundary cannot use `useI18n`, so it resolves the catalogue directly. Same precedence as the
    // provider — a crash must not silently switch the user's language.
    const i18n: I18n = makeI18n(readLocale())
    return (
      <div className="studio-page studio-failure" role="alert">
        <h1>
          <Icon name="warn" size={18} /> {i18n.t('shell.error.title', { where: this.props.where })}
        </h1>
        <p className="studio-failure-detail studio-mono studio-wrap-any">{error.message}</p>
        <p className="studio-muted">{i18n.t('shell.error.nothingSent')}</p>
        <button type="button" className="studio-btn" onClick={this.retry}>
          <Icon name="refresh" size={13} /> {i18n.t('common.retry')}
        </button>
      </div>
    )
  }
}
