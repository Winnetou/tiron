import { useEffect, useState } from 'react'
import { getMissingHyphens, type MissingHyphen } from './api'

/** The page whose line ends are looked at. */
export interface HyphenPage {
  editionId: string
  page: number
}

/** One line end that may get its hyphen back, and what became of it. */
interface Row {
  /** A number of its own, as the offsets of a row change. */
  id: number
  hyphen: MissingHyphen
  /** Is it among the places that "Approve all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'approved' | 'failed'
  error?: string
}

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; rows: Row[] }

const codePoints = (text: string) => Array.from(text).length

/** The places that are sure are taken by default: not those where both parts are words. */
const rowsOf = (found: MissingHyphen[]): Row[] =>
  found.map((hyphen, id) => ({
    id,
    hyphen,
    include: !hyphen.ambiguous,
    state: 'waiting',
  }))

/**
 * What the rows that follow the approved ones are moved by: a place is moved by
 * the change in length of every approved place before it on the page.
 */
function shifted(rows: Row[], approved: Row[]): Row[] {
  const done = new Set(approved.map((row) => row.id))
  return rows.map((row) => {
    if (done.has(row.id)) return row
    const shift = approved
      .filter((other) => other.hyphen.end <= row.hyphen.start)
      .reduce(
        (sum, other) =>
          sum +
          codePoints(other.hyphen.suggestion) -
          codePoints(other.hyphen.text),
        0,
      )
    return shift === 0
      ? row
      : {
          ...row,
          hyphen: {
            ...row.hyphen,
            start: row.hyphen.start + shift,
            end: row.hyphen.end + shift,
          },
        }
  })
}

interface Props {
  page: HyphenPage
  /**
   * Put the hyphens back at these places of the page (one request, all or none).
   * Rejects with the reason if the backend refuses.
   */
  onApprove: (hyphens: MissingHyphen[]) => Promise<void>
  /**
   * The place the user points at (hovers or focuses), as the stretch of the text
   * to show: the word at the end of the line and the one that starts the next.
   * Null when no place is pointed at.
   */
  onPoint: (stretch: { start: number; end: number } | null) => void
  onClose: () => void
}

/**
 * The panel with the line ends of the page on the screen where the OCR dropped
 * a hyphen (`GET .../missing-hyphens`): for each, the word at the end of the line,
 * the word that starts the next one, the word with its hyphen and the word that
 * the two parts make together (what the human decides on). Each has an
 * Approve button, which puts the hyphen back at that place alone, and a box to take
 * it out of "Approve all", which puts them back at all the places that are in, in
 * one request. The places where both parts are words on their own (`do` / `mu`)
 * are out by default, as it is not sure the word is split. Only this page is
 * looked at, never the edition. Nothing is changed until the user approves.
 *
 * The places are on the page that the user is correcting, so the panel is not a
 * box over the page: it is docked at the bottom of the text, which stays in sight
 * and can be used above it, as does the scan. Its height is changed by dragging
 * its lower right corner, and it folds to its header. The place that the user
 * points at is shown in the text.
 */
export function MissingHyphensReview({
  page,
  onApprove,
  onPoint,
  onClose,
}: Props) {
  const { editionId, page: number } = page
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })
  // "Approve all" refused: it is one request, so none of the places is changed.
  const [allError, setAllError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const [folded, setFolded] = useState(false)

  useEffect(() => {
    let current = true
    getMissingHyphens(editionId, number)
      .then(
        (found) => current && setLoaded({ kind: 'done', rows: rowsOf(found) }),
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
  }, [editionId, number])

  // Nothing is pointed at once the panel is gone.
  useEffect(() => () => onPoint(null), [onPoint])

  /** The stretch to show for a place: its word, the line break and the next word. */
  const stretchOf = (hyphen: MissingHyphen) => ({
    start: hyphen.start,
    end: hyphen.end + 1 + codePoints(hyphen.next_word),
  })

  function update(change: (rows: Row[]) => Row[]) {
    setLoaded((now) =>
      now.kind === 'done' ? { kind: 'done', rows: change(now.rows) } : now,
    )
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const todo = rows.filter((row) => row.include && row.state !== 'approved')

  /** Put the hyphens back at some places, in one request. */
  async function approve(batch: Row[]) {
    const ids = new Set(batch.map((row) => row.id))
    setWorking(true)
    setAllError(null)
    update((all) =>
      all.map((row) =>
        ids.has(row.id) ? { ...row, state: 'working', error: undefined } : row,
      ),
    )
    try {
      await onApprove(batch.map((row) => row.hyphen))
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Request failed'
      update((all) =>
        all.map((row) =>
          ids.has(row.id)
            ? { ...row, state: batch.length === 1 ? 'failed' : 'waiting', error: message }
            : row,
        ),
      )
      if (batch.length > 1) setAllError(message)
      setWorking(false)
      return
    }
    // The page is longer now: what follows the places on it moves with them, and
    // the stretch that was shown is not where it was.
    onPoint(null)
    update((all) =>
      shifted(
        all.map((row) => (ids.has(row.id) ? { ...row, state: 'approved' } : row)),
        batch,
      ),
    )
    setWorking(false)
  }

  function include(change: (row: Row) => boolean) {
    update((all) => all.map((row) => ({ ...row, include: change(row) })))
  }

  const sure = rows.filter((row) => !row.hyphen.ambiguous).length
  const approved = rows.filter((row) => row.state === 'approved').length
  const failed = rows.filter((row) => row.state === 'failed').length

  return (
    <section
      aria-label="The missing hyphens on this page"
      className={`review-dock${folded ? ' folded' : ''}`}
    >
      <header className="review-header">
        <h2>The missing hyphens on page {number}</h2>
        <span className="review-header-buttons">
          <button
            type="button"
            aria-expanded={!folded}
            onClick={() => setFolded(!folded)}
          >
            {folded ? 'Show the list' : 'Fold'}
          </button>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </span>
      </header>
      {!folded && (
        <div className="review-body">
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the missing hyphens on this page …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && rows.length === 0 && (
            <p role="status">No missing hyphen found on this page.</p>
          )}
          {allError && <p role="alert">{allError}</p>}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'place' : 'places'}, {sure} of them
                sure
                {approved > 0 && `; ${approved} approved`}
                {failed > 0 && `; ${failed} failed`}
              </p>
              <div className="review-toolbar">
                <button
                  type="button"
                  className="review-approve-all"
                  disabled={working || todo.length === 0}
                  onClick={() => approve(todo)}
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
                  onClick={() => include((row) => !row.hyphen.ambiguous)}
                >
                  Include only the sure ones
                </button>
              </div>
              <ul className="review-list">
                {rows.map((row) => {
                  const { hyphen } = row
                  return (
                    <li
                      key={row.id}
                      className={`review-row ${row.state}${hyphen.ambiguous ? ' misfit' : ''}`}
                      onMouseEnter={() =>
                        row.state !== 'approved' && onPoint(stretchOf(hyphen))
                      }
                      onMouseLeave={() => onPoint(null)}
                      onFocus={() =>
                        row.state !== 'approved' && onPoint(stretchOf(hyphen))
                      }
                      onBlur={() => onPoint(null)}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Include ${hyphen.text} ${hyphen.next_word} in Approve all`}
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
                      <span className="misspelling">{hyphen.text}</span>
                      <span aria-hidden="true"> ↵ </span>
                      <span>{hyphen.next_word}</span>
                      <span aria-hidden="true"> → </span>
                      <span className="suggestion">{hyphen.suggestion}</span>
                      <span className="review-joined">
                        {' '}
                        (joined: <strong>{hyphen.joined}</strong>)
                      </span>
                      {hyphen.ambiguous && (
                        <span className="review-misfit-note">
                          both parts are words: not sure it is split
                        </span>
                      )}
                      {row.state === 'approved' ? (
                        <span className="review-done">✓ Approved</span>
                      ) : (
                        <button
                          type="button"
                          aria-label={`Approve ${hyphen.suggestion} before ${hyphen.next_word}`}
                          data-edition-id={editionId}
                          data-page={number}
                          data-start={hyphen.start}
                          data-end={hyphen.end}
                          disabled={working}
                          onClick={() => approve([row])}
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
      )}
    </section>
  )
}
