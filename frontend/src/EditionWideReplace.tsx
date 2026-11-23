import { useEffect, useRef, useState } from 'react'
import {
  getTokenOccurrences,
  type PageTokenOccurrences,
  type TokenOccurrence,
} from './api'

/** One place of the token, and what became of it. */
interface Row {
  /** A number of its own, as the offsets of a row change. */
  id: number
  page: number
  place: TokenOccurrence
  /** Is it among the places that "Replace all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'replaced' | 'failed'
  error?: string
  /** What went wrong after the place was corrected (the legal spelling). */
  warning?: string
}

/** What was looked for and what it is to be replaced with, as it was sent. */
interface Found {
  token: string
  replacement: string
}

type Loaded =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; found: Found; rows: Row[] }

const codePoints = (text: string) => Array.from(text).length

function rowsOf(pages: PageTokenOccurrences[]): Row[] {
  let id = 0
  return pages.flatMap(({ page_number, occurrences }) =>
    occurrences.map((place) => ({
      id: id++,
      page: page_number,
      place,
      include: true,
      state: 'waiting' as const,
    })),
  )
}

interface Props {
  editionId: string
  /**
   * Correct a place of a page: the token at the offsets becomes the replacement
   * (the same request as the correction of a word), which is made a legal spelling
   * of the edition. Rejects with the reason if the backend refuses; resolves with a
   * warning if only the legal spelling was refused.
   */
  onApprove: (
    page: number,
    token: { text: string; start: number; end: number; suggestion: string },
  ) => Promise<string | undefined>
  onClose: () => void
}

/**
 * The big box to correct a token in the whole edition. The OCR makes the same
 * mistake again and again, and the spell check does not flag it when the wrong word
 * is a word too ("tem" is read as "tern"). The user, who sees the scan, gives the
 * token as it is wrong in one box and what it is to be replaced with in another. The
 * backend finds every place of the token, exactly (`GET .../token-occurrences`), and
 * the box lists them by page, each in its context: the token before it, the token
 * with the change, and the token after it. Each has a Replace button, which corrects
 * that place alone (it carries the edition, the page and the offsets), and a box to
 * take it out of "Replace all". Nothing is changed until the user replaces.
 *
 * As in the box for the same mistake in the edition, one replacement is made at a
 * time: it changes the length of the page, so the offsets of the places that follow
 * it on its page are moved by the difference. The box remembers what it looked for
 * and what it replaces with, so changing a box does not change the list: Find again.
 */
export function TokenReplaceReview({ editionId, onApprove, onClose }: Props) {
  const [token, setToken] = useState('')
  const [replacement, setReplacement] = useState('')
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'idle' })
  // The rows as they are now, for "Replace all", which goes on for a while.
  const latest = useRef<Row[]>([])
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  )

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const wrong = token.trim()
  const right = replacement.trim()
  const ready = wrong !== '' && right !== '' && wrong !== right

  function find() {
    setLoaded({ kind: 'loading' })
    getTokenOccurrences(editionId, wrong)
      .then((pages) => {
        latest.current = rowsOf(pages)
        setLoaded({
          kind: 'done',
          found: { token: wrong, replacement: right },
          rows: latest.current,
        })
      })
      .catch((error: unknown) =>
        setLoaded({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Request failed',
        }),
      )
  }

  function update(change: (rows: Row[]) => Row[]) {
    latest.current = change(latest.current)
    setLoaded((now) =>
      now.kind === 'done' ? { ...now, rows: latest.current } : now,
    )
  }

  const found = loaded.kind === 'done' ? loaded.found : null

  async function replace(row: Row): Promise<boolean> {
    if (!found) return false
    update((all) =>
      all.map((r) =>
        r.id === row.id ? { ...r, state: 'working', error: undefined } : r,
      ),
    )
    let warning: string | undefined
    try {
      warning = await onApprove(row.page, {
        text: row.place.text,
        start: row.place.start,
        end: row.place.end,
        suggestion: found.replacement,
      })
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
    // The page is longer or shorter now: what follows on it moves with it.
    const shift = codePoints(found.replacement) - codePoints(row.place.text)
    update((all) =>
      all.map((other) => {
        if (other.id === row.id) return { ...other, state: 'replaced', warning }
        if (other.page !== row.page || other.place.start < row.place.end) return other
        const { start, end } = other.place
        return {
          ...other,
          place: { ...other.place, start: start + shift, end: end + shift },
        }
      }),
    )
    return true
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const working = rows.some((row) => row.state === 'working') || progress !== null
  const busy = working || loaded.kind === 'loading'
  const todo = rows.filter((row) => row.include && row.state !== 'replaced')
  const pages = [...new Set(rows.map((row) => row.page))]

  async function replaceAll() {
    const batch = todo
    setProgress({ done: 0, total: batch.length })
    let done = 0
    for (const row of batch) {
      // (the offsets of the row are those of the moment: earlier replacements moved them)
      await replace(latest.current.find((r) => r.id === row.id) ?? row)
      setProgress({ done: ++done, total: batch.length })
    }
    setProgress(null)
  }

  function include(change: (row: Row) => boolean) {
    update((all) => all.map((row) => ({ ...row, include: change(row) })))
  }

  const replaced = rows.filter((row) => row.state === 'replaced').length
  const failed = rows.filter((row) => row.state === 'failed').length

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Replace a token in the edition"
        className="review-box review-styled"
      >
        <header className="review-header">
          <h2>Replace a token in the edition</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          <form
            className="review-chunk"
            onSubmit={(event) => {
              event.preventDefault()
              if (ready && !busy) find()
            }}
          >
            <input
              aria-label="Incorrect token"
              placeholder="Incorrect token, e.g. tern"
              maxLength={100}
              spellCheck={false}
              value={token}
              disabled={working}
              onChange={(event) => setToken(event.target.value)}
            />
            <input
              aria-label="Replacement token"
              placeholder="Replacement token, e.g. tem"
              maxLength={100}
              spellCheck={false}
              value={replacement}
              disabled={working}
              onChange={(event) => setReplacement(event.target.value)}
            />
            <button
              type="submit"
              className="review-find"
              disabled={!ready || busy}
              title={
                ready
                  ? undefined
                  : 'Write the incorrect token and a different replacement first'
              }
            >
              Find
            </button>
          </form>
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the token in the edition …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {found && rows.length === 0 && (
            <p role="status">The token “{found.token}” is nowhere in the edition.</p>
          )}
          {found && rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'place' : 'places'} of “{found.token}” on{' '}
                {pages.length} {pages.length === 1 ? 'page' : 'pages'}
                {replaced > 0 && `; ${replaced} replaced`}
                {failed > 0 && `; ${failed} failed`}
              </p>
              <div className="review-toolbar">
                <button
                  type="button"
                  className="review-approve-all"
                  disabled={working || todo.length === 0}
                  onClick={replaceAll}
                >
                  Replace all ({todo.length})
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
                {progress && (
                  <span role="status">
                    Replacing {progress.done} of {progress.total} …
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
                        <li key={row.id} className={`review-row ${row.state}`}>
                          <input
                            type="checkbox"
                            aria-label={`Include ${row.place.text} on page ${page} at ${row.place.start} in Replace all`}
                            checked={row.include}
                            disabled={working || row.state === 'replaced'}
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
                          <span className="review-trigram">
                            <span className="review-context">{row.place.before}</span>{' '}
                            <span className="misspelling">{row.place.text}</span>
                            <span aria-hidden="true"> → </span>
                            <mark>{found.replacement}</mark>{' '}
                            <span className="review-context">{row.place.after}</span>
                          </span>
                          {row.state === 'replaced' ? (
                            <span className="review-done">✓ Replaced</span>
                          ) : (
                            <button
                              type="button"
                              aria-label={`Replace ${row.place.text} with ${found.replacement} on page ${page} at ${row.place.start}`}
                              data-edition-id={editionId}
                              data-page={page}
                              data-start={row.place.start}
                              data-end={row.place.end}
                              disabled={working}
                              onClick={() => replace(row)}
                            >
                              Replace
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
            </>
          )}
        </div>
      </div>
    </div>
  )
}
