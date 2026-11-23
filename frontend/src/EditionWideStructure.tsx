import { useEffect, useState } from 'react'
import {
  getSimilarPageNumbers,
  type PageNumberMatch,
  type SimilarPageNumbers,
} from './api'

/*
 * Only the page number is looked for on the other pages for now. The same box can
 * serve the running header and the footer of a page later (a human marks one, the
 * same line on the other pages is offered for approval); that is left for when
 * this has proved itself, and is not done here.
 */

/** The page number that is marked on a page: the example for the other pages. */
export interface PageNumberExample {
  editionId: string
  /** The number of the page it is marked on. */
  page: number
}

/** One page that may get a page number, and what became of it. */
interface Row {
  /** A number of its own. */
  id: number
  match: PageNumberMatch
  /** Is it among the pages that "Approve all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'approved' | 'failed'
  error?: string
}

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; found: SimilarPageNumbers; rows: Row[] }

/** Only the pages whose number follows the numbering are taken by default. */
const rowsOf = (found: SimilarPageNumbers): Row[] =>
  found.candidates.map((match, id) => ({
    id,
    match,
    include: match.fits_sequence,
    state: 'waiting',
  }))

interface Props {
  example: PageNumberExample
  /**
   * Mark the page number on a page: what is at the offsets is the page number.
   * Rejects with the reason if the backend refuses.
   */
  onApprove: (match: PageNumberMatch) => Promise<void>
  onClose: () => void
}

/**
 * The big box with the pages of the edition that have a page number like the one
 * that is marked on the page on the screen (`GET .../similar-page-numbers`): for
 * each, the page, the number as printed, the line next to it, and whether the
 * number follows the numbering of the example. Each has an Approve button, which
 * marks the page number on that page alone, and a box to take it out of "Approve
 * all", which marks all of the pages that are in. Those that do not follow the
 * numbering are out by default, as they are probably something else (a table of
 * contents, say). Nothing is marked until the user approves.
 */
export function SimilarPageNumbersReview({ example, onApprove, onClose }: Props) {
  const { editionId, page } = example
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })
  // "Approve all" going through the pages: how far it is, of how many.
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  )

  useEffect(() => {
    let current = true
    getSimilarPageNumbers(editionId, page)
      .then(
        (found) =>
          current && setLoaded({ kind: 'done', found, rows: rowsOf(found) }),
      )
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
  }, [editionId, page])

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
        ? { ...now, rows: now.rows.map((row) => (row.id === id ? change(row) : row)) }
        : now,
    )
  }

  /** Approve one page; true if it worked. */
  async function approve(row: Row): Promise<boolean> {
    update(row.id, (r) => ({ ...r, state: 'working', error: undefined }))
    try {
      await onApprove(row.match)
    } catch (error) {
      update(row.id, (r) => ({
        ...r,
        state: 'failed',
        error: error instanceof Error ? error.message : 'Request failed',
      }))
      return false
    }
    update(row.id, (r) => ({ ...r, state: 'approved' }))
    return true
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const working = rows.some((row) => row.state === 'working') || progress !== null
  const todo = rows.filter((row) => row.include && row.state !== 'approved')

  async function approveAll() {
    const batch = todo
    setProgress({ done: 0, total: batch.length })
    let done = 0
    for (const row of batch) {
      await approve(row)
      setProgress({ done: ++done, total: batch.length })
    }
    setProgress(null)
  }

  function include(change: (row: Row) => boolean) {
    setLoaded((now) =>
      now.kind === 'done'
        ? { ...now, rows: now.rows.map((row) => ({ ...row, include: change(row) })) }
        : now,
    )
  }

  const fitting = rows.filter((row) => row.match.fits_sequence).length
  const approved = rows.filter((row) => row.state === 'approved').length
  const failed = rows.filter((row) => row.state === 'failed').length

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="The same page number on the other pages"
        className="review-box review-styled"
      >
        <header className="review-header">
          <h2>The same page number on the other pages</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the same page number in the edition …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && (
            <p role="status" className="review-example">
              The example is page {loaded.found.example.page_number}: “
              {loaded.found.example.text}” on the {loaded.found.where} line, so the
              printed number is the number of the page less {loaded.found.offset}.
            </p>
          )}
          {loaded.kind === 'done' && rows.length === 0 && (
            <p role="status">No other page has a page number like it.</p>
          )}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'page' : 'pages'}, {fitting} of them
                with a number that follows the numbering
                {approved > 0 && `; ${approved} approved`}
                {failed > 0 && `; ${failed} failed`}
              </p>
              <div className="review-toolbar">
                <button
                  type="button"
                  className="review-approve-all"
                  disabled={working || todo.length === 0}
                  onClick={approveAll}
                >
                  Approve all ({todo.length})
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() => include(() => true)}
                >
                  Include all
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() => include(() => false)}
                >
                  Include none
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() => include((row) => row.match.fits_sequence)}
                >
                  Include only those that follow the numbering
                </button>
                {progress && (
                  <span role="status">
                    Approving {progress.done} of {progress.total} …
                  </span>
                )}
              </div>
              <ul className="review-list">
                {rows.map((row) => {
                  const { match } = row
                  return (
                    <li
                      key={row.id}
                      className={`review-row ${row.state}${match.fits_sequence ? '' : ' misfit'}`}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Include page ${match.page_number} in Approve all`}
                        checked={row.include}
                        disabled={working || row.state === 'approved'}
                        onChange={(event) =>
                          update(row.id, (r) => ({
                            ...r,
                            include: event.target.checked,
                          }))
                        }
                      />
                      <span className="review-page">Page {match.page_number}</span>
                      <span className="suggestion">“{match.text}”</span>
                      {match.fits_sequence ? (
                        <span className="review-fits">fits</span>
                      ) : (
                        <span className="review-misfit-note">
                          does not follow the numbering: expected{' '}
                          {match.page_number - (loaded.kind === 'done' ? loaded.found.offset : 0)}
                        </span>
                      )}
                      {match.context && (
                        <span className="review-context">{match.context}</span>
                      )}
                      {row.state === 'approved' ? (
                        <span className="review-done">✓ Approved</span>
                      ) : (
                        <button
                          type="button"
                          aria-label={`Approve “${match.text}” on page ${match.page_number}`}
                          data-edition-id={editionId}
                          data-page={match.page_number}
                          data-start={match.start}
                          data-end={match.end}
                          disabled={working}
                          onClick={() => approve(row)}
                        >
                          Approve
                        </button>
                      )}
                      {row.state === 'failed' && (
                        <span role="alert" className="review-error">
                          {row.error}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
