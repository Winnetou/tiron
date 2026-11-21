import { useEffect, useState } from 'react'
import {
  getSimilarMisspellings,
  type PageMatches,
  type TokenMatch,
} from './api'

/** A correction that the user made, which may be applied to the whole edition. */
export interface EditionCorrection {
  editionId: string
  /** The spelling that was wrong, and what it was corrected to. */
  incorrect: string
  corrected: string
}

interface OfferProps {
  correction: EditionCorrection
  onApply: () => void
  onDismiss: () => void
}

/**
 * The small box that comes up after a word was corrected: shall the same
 * correction be applied to the entire edition? "Apply it to the entire edition"
 * opens the list of the places (`SimilarMisspellings`); "Nein Danke" closes the
 * box.
 */
export function ApplyOffer({ correction, onApply, onDismiss }: OfferProps) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onDismiss()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onDismiss])

  return (
    <div
      role="dialog"
      aria-label="Apply the correction to the entire edition"
      className="offer-box"
    >
      <p>
        You corrected <strong>{correction.incorrect}</strong> to{' '}
        <strong>{correction.corrected}</strong>. The OCR may have made the same
        mistake in other places of the edition.
      </p>
      <div className="offer-buttons">
        <button type="button" onClick={onApply}>
          Apply it to the entire edition
        </button>
        <button type="button" onClick={onDismiss}>
          Nein Danke
        </button>
      </div>
    </div>
  )
}

/** One place of the edition that has the mistake, and what became of it. */
interface Row {
  /** A number of its own, as the offsets of a row change. */
  id: number
  page: number
  token: TokenMatch
  state: 'waiting' | 'working' | 'approved' | 'failed'
  error?: string
  /** What went wrong after the place was corrected (the legal spelling). */
  warning?: string
}

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; rows: Row[] }

const codePoints = (text: string) => Array.from(text).length

function rowsOf(pages: PageMatches[]): Row[] {
  let id = 0
  return pages.flatMap(({ page_number, tokens }) =>
    tokens.map((token) => ({
      id: id++,
      page: page_number,
      token,
      state: 'waiting' as const,
    })),
  )
}

interface ReviewProps {
  correction: EditionCorrection
  /**
   * Apply the correction of a place: the token of the page at its offsets
   * becomes its suggestion, and the suggestion is made a legal spelling. Rejects
   * with the reason if the backend refuses the correction; resolves with a
   * warning if only the legal spelling was refused.
   */
  onApprove: (page: number, token: TokenMatch) => Promise<string | undefined>
  onClose: () => void
}

/**
 * The big box with the places of the edition that have the same mistake as the
 * correction (`GET /editions/{id}/similar-misspellings`): for each, the page, the
 * misspelling and the correction that the backend suggests, and an Approve button
 * that applies it to that place alone, and makes the suggestion a legal spelling
 * of the edition. The button carries the edition, the page and the offsets it is
 * about. One approval is made at a time, as it changes the
 * offsets of the places that follow it on its page: those are moved by the
 * difference in length.
 */
export function SimilarMisspellings({
  correction,
  onApprove,
  onClose,
}: ReviewProps) {
  const { editionId, incorrect, corrected } = correction
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })

  useEffect(() => {
    let current = true
    getSimilarMisspellings(editionId, incorrect, corrected)
      .then((pages) => current && setLoaded({ kind: 'done', rows: rowsOf(pages) }))
      .catch(
        (error: unknown) =>
          current &&
          setLoaded({
            kind: 'error',
            message: error instanceof Error ? error.message : 'Request failed',
          }),
      )
    return () => {
      current = false
    }
  }, [editionId, incorrect, corrected])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  function update(id: number, change: (row: Row) => Row) {
    setLoaded((now) =>
      now.kind === 'done'
        ? { kind: 'done', rows: now.rows.map((row) => (row.id === id ? change(row) : row)) }
        : now,
    )
  }

  async function approve(row: Row) {
    update(row.id, (r) => ({ ...r, state: 'working', error: undefined }))
    let warning: string | undefined
    try {
      warning = await onApprove(row.page, row.token)
    } catch (error) {
      update(row.id, (r) => ({
        ...r,
        state: 'failed',
        error: error instanceof Error ? error.message : 'Request failed',
      }))
      return
    }
    // The page is longer or shorter now: what follows on it moves with it.
    const shift = codePoints(row.token.suggestion) - codePoints(row.token.text)
    setLoaded((now) =>
      now.kind === 'done'
        ? {
            kind: 'done',
            rows: now.rows.map((other) => {
              if (other.id === row.id) return { ...other, state: 'approved', warning }
              if (other.page !== row.page || other.token.start < row.token.end) {
                return other
              }
              const { start, end } = other.token
              return {
                ...other,
                token: { ...other.token, start: start + shift, end: end + shift },
              }
            }),
          }
        : now,
    )
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const working = rows.some((row) => row.state === 'working')
  const pages = [...new Set(rows.map((row) => row.page))]

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="The same mistake in the edition"
        className="review-box"
      >
        <header className="review-header">
          <h2>
            The same mistake in the edition: <strong>{incorrect}</strong> →{' '}
            <strong>{corrected}</strong>
          </h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the same mistake in the edition …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && rows.length === 0 && (
            <p role="status">No other place in the edition has this mistake.</p>
          )}
          {rows.length > 0 && (
            <p role="status">
              {rows.length} {rows.length === 1 ? 'place' : 'places'} on{' '}
              {pages.length} {pages.length === 1 ? 'page' : 'pages'}
            </p>
          )}
          {pages.map((page) => (
            <section key={page} aria-label={`Page ${page}`}>
              <h3>Page {page}</h3>
              <ul className="review-list">
                {rows
                  .filter((row) => row.page === page)
                  .map((row) => (
                    <li key={row.id} className={`review-row ${row.state}`}>
                      <span className="misspelling">{row.token.text}</span>
                      <span aria-hidden="true"> → </span>
                      <span className="suggestion">{row.token.suggestion}</span>
                      <span className="review-place">
                        {row.token.start}–{row.token.end}
                      </span>
                      {row.state === 'approved' ? (
                        <span className="review-done">✓ Approved</span>
                      ) : (
                        <button
                          type="button"
                          aria-label={`Approve ${row.token.text} → ${row.token.suggestion} on page ${page}`}
                          data-edition-id={editionId}
                          data-page={page}
                          data-start={row.token.start}
                          data-end={row.token.end}
                          disabled={working}
                          onClick={() => approve(row)}
                        >
                          Approve
                        </button>
                      )}
                      {row.warning && (
                        <span role="status" className="review-warning">
                          {row.warning}
                        </span>
                      )}
                      {row.state === 'failed' && (
                        <span role="alert" className="review-error">
                          {row.error}
                        </span>
                      )}
                    </li>
                  ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
