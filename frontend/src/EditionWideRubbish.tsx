import { useEffect, useRef, useState } from 'react'
import { getRubbishLines, type PageRubbish, type RubbishLine } from './api'

/** The edition whose lines are looked at. */
export interface RubbishEdition {
  editionId: string
}

/** One line that may be removed, and what became of it. */
interface Row {
  /** A number of its own, as the offsets of a row change. */
  id: number
  page: number
  line: RubbishLine
  /** Is it among the lines that "Approve all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'approved' | 'failed'
  error?: string
}

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; rows: Row[] }

/** The lines that are sure are taken by default; those with a real word in are not. */
function rowsOf(pages: PageRubbish[]): Row[] {
  let id = 0
  return pages.flatMap(({ page_number, lines }) =>
    lines.map((line) => ({
      id: id++,
      page: page_number,
      line,
      include: line.sure,
      state: 'waiting' as const,
    })),
  )
}

interface Props {
  edition: RubbishEdition
  /**
   * Remove a line of a page. Resolves with how many characters of the page's text
   * went with it (the line and its line break); rejects with the reason if the
   * backend refuses.
   */
  onApprove: (page: number, line: RubbishLine) => Promise<number>
  onClose: () => void
}

/**
 * The big box with the lines of the edition that are OCR rubbish
 * (`GET /editions/{id}/rubbish-lines`): an ornament that was read as letters and
 * signs. For each, the page and the line, with an Approve button that removes that
 * line alone (it carries the edition, the page and the offsets it is about) and a
 * box to take it out of "Approve all", which removes all the lines that are in.
 * The lines that have a correct word in them may be text with a lot of punctuation:
 * they are out by default. Nothing is removed until the user approves.
 *
 * One removal is made at a time, as it moves the lines that follow it on its page:
 * their offsets are moved by the number of characters that went. A line that the
 * backend refuses (the page has changed, say) is marked, and "Approve all" goes on
 * with the others.
 */
export function RubbishReview({ edition, onApprove, onClose }: Props) {
  const { editionId } = edition
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })
  // The rows as they are now, for "Approve all", which goes on for a while: the
  // offsets of a row are moved by every removal before it on its page.
  const latest = useRef<Row[]>([])
  // "Approve all" going through the lines: how far it is, of how many.
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  )

  useEffect(() => {
    let current = true
    getRubbishLines(editionId)
      .then((pages) => {
        if (!current) return
        latest.current = rowsOf(pages)
        setLoaded({ kind: 'done', rows: latest.current })
      })
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
  }, [editionId])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  function update(change: (rows: Row[]) => Row[]) {
    latest.current = change(latest.current)
    setLoaded({ kind: 'done', rows: latest.current })
  }

  /** Remove one line; true if it worked. */
  async function approve(row: Row): Promise<boolean> {
    update((all) =>
      all.map((r) =>
        r.id === row.id ? { ...r, state: 'working', error: undefined } : r,
      ),
    )
    let removed: number
    try {
      removed = await onApprove(row.page, row.line)
    } catch (error) {
      update((all) =>
        all.map((r) =>
          r.id === row.id
            ? {
                ...r,
                state: 'failed',
                error: error instanceof Error ? error.message : 'Request failed',
              }
            : r,
        ),
      )
      return false
    }
    // The page is shorter now: the lines that follow on it move up.
    update((all) =>
      all.map((other) => {
        if (other.id === row.id) return { ...other, state: 'approved' }
        if (other.page !== row.page || other.line.start < row.line.end) return other
        const { start, end } = other.line
        return {
          ...other,
          line: { ...other.line, start: start - removed, end: end - removed },
        }
      }),
    )
    return true
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const working = rows.some((row) => row.state === 'working') || progress !== null
  const todo = rows.filter((row) => row.include && row.state !== 'approved')
  const pages = [...new Set(rows.map((row) => row.page))]

  async function approveAll() {
    const batch = todo
    setProgress({ done: 0, total: batch.length })
    let done = 0
    for (const row of batch) {
      // (the offsets of the row are those of the moment: earlier removals moved them)
      await approve(latest.current.find((r) => r.id === row.id) ?? row)
      setProgress({ done: ++done, total: batch.length })
    }
    setProgress(null)
  }

  function include(change: (row: Row) => boolean) {
    update((all) => all.map((row) => ({ ...row, include: change(row) })))
  }

  const sure = rows.filter((row) => row.line.sure).length
  const approved = rows.filter((row) => row.state === 'approved').length
  const failed = rows.filter((row) => row.state === 'failed').length

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="The OCR rubbish in the edition"
        className="review-box"
      >
        <header className="review-header">
          <h2>The OCR rubbish in the edition</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the OCR rubbish in the edition …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && rows.length === 0 && (
            <p role="status">No OCR rubbish found in the edition.</p>
          )}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'line' : 'lines'} on{' '}
                {pages.length} {pages.length === 1 ? 'page' : 'pages'}, {sure} of them
                sure
                {approved > 0 && `; ${approved} removed`}
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
                  onClick={() => include((row) => row.line.sure)}
                >
                  Include only the sure ones
                </button>
                {progress && (
                  <span role="status">
                    Removing {progress.done} of {progress.total} …
                  </span>
                )}
              </div>
              {pages.map((page) => (
                <section key={page} aria-label={`Page ${page}`}>
                  <h3>Page {page}</h3>
                  <ul className="review-list">
                    {rows
                      .filter((row) => row.page === page)
                      .map((row) => (
                        <li
                          key={row.id}
                          className={`review-row ${row.state}${row.line.sure ? '' : ' misfit'}`}
                        >
                          <input
                            type="checkbox"
                            aria-label={`Include the line “${row.line.text}” of page ${page} in Approve all`}
                            checked={row.include}
                            disabled={working || row.state === 'approved'}
                            onChange={(event) =>
                              update((all) =>
                                all.map((r) =>
                                  r.id === row.id
                                    ? { ...r, include: event.target.checked }
                                    : r,
                                ),
                              )
                            }
                          />
                          <span className="rubbish-line">{row.line.text}</span>
                          {!row.line.sure && (
                            <span className="review-misfit-note">
                              has a correct word: may be text
                            </span>
                          )}
                          {row.state === 'approved' ? (
                            <span className="review-done">✓ Removed</span>
                          ) : (
                            <button
                              type="button"
                              aria-label={`Remove the line “${row.line.text}” of page ${page}`}
                              data-edition-id={editionId}
                              data-page={page}
                              data-start={row.line.start}
                              data-end={row.line.end}
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
                      ))}
                  </ul>
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
