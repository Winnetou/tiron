import { useEffect, useState } from 'react'
import { getSpellingChunkMatches, type SpellingGroup } from './api'

/** One flagged spelling, and what became of it. */
interface Row {
  /** A number of its own. */
  id: number
  group: SpellingGroup
  /** Is it among the spellings that "Accept all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'accepted' | 'failed'
  error?: string
}

type Loaded =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; rows: Row[] }

/** The most endings of a chunk (the backend's limit too). */
const MOST_ENDINGS = 10

const rowsOf = (groups: SpellingGroup[]): Row[] =>
  groups.map((group, id) => ({ id, group, include: true, state: 'waiting' }))

interface Props {
  editionId: string
  /**
   * Accept a spelling as legal in the edition (the same request as the "legal
   * spelling" button of a word). Rejects with the reason if the backend refuses.
   */
  onAccept: (spelling: string) => Promise<void>
  onClose: () => void
}

/**
 * The big box to accept the spellings of the edition that have a chunk in common,
 * as legal spellings, without correcting anything. Old print has spellings that
 * repeat ("Konwencya", "tendencyę"): the dictionary flags them, and a person
 * accepts them one by one with the "legal spelling" button of a word. Here the
 * person gives the core of the chunk (`cy`) in a box and, below it, an ending in
 * another box (`a`); a plus sign adds a box for another ending (`i`, `ami`). The
 * backend (`GET .../spelling-chunk-matches`) finds the flagged words of the
 * edition that have `cya`, `cyi` or `cyami` in them, and the box lists each once,
 * with how many times it is in the edition and its first places, each in its
 * context: the token before it, the word, the token after it. Each has an Accept
 * button, which makes it a legal spelling of the edition, and a box to take it out
 * of "Accept all". Nothing is accepted until the user accepts.
 *
 * A legal spelling is of the word, wherever it is, so one acceptance is for all its
 * places: nothing moves, and the text of the pages is not changed.
 */
export function SpellingChunkReview({ editionId, onAccept, onClose }: Props) {
  const [core, setCore] = useState('')
  // The boxes for the endings: at least one.
  const [endings, setEndings] = useState<string[]>([''])
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'idle' })
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

  const trimmed = core.trim()
  // A box left empty is left out; if all are empty, the core alone is looked for.
  const given = endings.map((ending) => ending.trim()).filter((ending) => ending !== '')
  const sent = given.length > 0 ? given : ['']
  const substrings = [...new Set(sent.map((ending) => trimmed + ending))]

  function find() {
    setLoaded({ kind: 'loading' })
    getSpellingChunkMatches(editionId, trimmed, sent)
      .then((groups) => setLoaded({ kind: 'done', rows: rowsOf(groups) }))
      .catch((error: unknown) =>
        setLoaded({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Request failed',
        }),
      )
  }

  function update(change: (rows: Row[]) => Row[]) {
    setLoaded((now) =>
      now.kind === 'done' ? { kind: 'done', rows: change(now.rows) } : now,
    )
  }

  async function accept(row: Row): Promise<boolean> {
    update((all) =>
      all.map((r) =>
        r.id === row.id ? { ...r, state: 'working', error: undefined } : r,
      ),
    )
    try {
      await onAccept(row.group.text)
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
    update((all) =>
      all.map((r) => (r.id === row.id ? { ...r, state: 'accepted' } : r)),
    )
    return true
  }

  const rows = loaded.kind === 'done' ? loaded.rows : []
  const working = rows.some((row) => row.state === 'working') || progress !== null
  const todo = rows.filter((row) => row.include && row.state !== 'accepted')

  async function acceptAll() {
    const batch = todo
    setProgress({ done: 0, total: batch.length })
    let done = 0
    for (const row of batch) {
      await accept(row)
      setProgress({ done: ++done, total: batch.length })
    }
    setProgress(null)
  }

  function include(change: (row: Row) => boolean) {
    update((all) => all.map((row) => ({ ...row, include: change(row) })))
  }

  const accepted = rows.filter((row) => row.state === 'accepted').length
  const failed = rows.filter((row) => row.state === 'failed').length
  const places = rows.reduce((sum, row) => sum + row.group.count, 0)
  const busy = working || loaded.kind === 'loading'

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Accept spellings of the edition"
        className="review-box"
      >
        <header className="review-header">
          <h2>Accept spellings of the edition</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          <form
            className="review-chunk"
            onSubmit={(event) => {
              event.preventDefault()
              if (trimmed !== '' && !busy) find()
            }}
          >
            <label>
              Core of the chunk
              <input
                aria-label="Core of the chunk"
                placeholder="cy"
                size={12}
                maxLength={30}
                spellCheck={false}
                value={core}
                disabled={working}
                onChange={(event) => setCore(event.target.value)}
              />
            </label>
            <div className="review-endings">
              {endings.map((ending, index) => (
                <div key={index} className="review-ending">
                  <label>
                    {index === 0 ? 'Ending of the chunk' : `Ending ${index + 1}`}
                    <input
                      aria-label={`Ending ${index + 1}`}
                      placeholder={['a', 'i', 'ami'][index] ?? ''}
                      size={12}
                      maxLength={30}
                      spellCheck={false}
                      value={ending}
                      disabled={working}
                      onChange={(event) =>
                        setEndings((now) =>
                          now.map((e, i) => (i === index ? event.target.value : e)),
                        )
                      }
                    />
                  </label>
                  {index > 0 && (
                    <button
                      type="button"
                      aria-label={`Remove ending ${index + 1}`}
                      disabled={working}
                      onClick={() =>
                        setEndings((now) => now.filter((_, i) => i !== index))
                      }
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                className="review-add-ending"
                aria-label="Add another ending"
                title="Add another ending"
                disabled={working || endings.length >= MOST_ENDINGS}
                onClick={() => setEndings((now) => [...now, ''])}
              >
                +
              </button>
            </div>
            {trimmed !== '' && (
              <p className="review-context">
                Looking for words that have <strong>{substrings.join(', ')}</strong> in
                them.
              </p>
            )}
            <button type="submit" disabled={trimmed === '' || busy}>
              Find
            </button>
          </form>
          {loaded.kind === 'loading' && (
            <p role="status">Looking for the words in the edition …</p>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && rows.length === 0 && (
            <p role="status">No flagged word of the edition has this chunk.</p>
          )}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'spelling' : 'spellings'}, {places}{' '}
                {places === 1 ? 'place' : 'places'} in the edition
                {accepted > 0 && `; ${accepted} accepted`}
                {failed > 0 && `; ${failed} failed`}
              </p>
              <div className="review-toolbar">
                <button
                  type="button"
                  className="review-approve-all"
                  disabled={working || todo.length === 0}
                  onClick={acceptAll}
                >
                  Accept all ({todo.length})
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
                    Accepting {progress.done} of {progress.total} …
                  </span>
                )}
              </div>
              <ul className="review-list">
                {rows.map((row) => {
                  const { group } = row
                  return (
                    <li key={row.id} className={`review-row ${row.state}`}>
                      <input
                        type="checkbox"
                        aria-label={`Include ${group.text} in Accept all`}
                        checked={row.include}
                        disabled={working || row.state === 'accepted'}
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
                      <span className="review-spelling">
                        <strong>{group.text}</strong>{' '}
                        <span className="review-context">
                          ×{group.count}
                        </span>
                        <ul className="review-trigrams">
                          {group.occurrences.map((place) => (
                            <li key={`${place.page_number}:${place.start}`}>
                              <span className="review-context">{place.before}</span>{' '}
                              <mark>{group.text}</mark>{' '}
                              <span className="review-context">{place.after}</span>{' '}
                              <span className="review-page">
                                (page {place.page_number})
                              </span>
                            </li>
                          ))}
                          {group.count > group.occurrences.length && (
                            <li className="review-context">
                              and {group.count - group.occurrences.length} more
                            </li>
                          )}
                        </ul>
                      </span>
                      {row.state === 'accepted' ? (
                        <span className="review-done">✓ Accepted</span>
                      ) : (
                        <button
                          type="button"
                          aria-label={`Accept ${group.text} as a legal spelling`}
                          data-edition-id={editionId}
                          data-spelling={group.text}
                          disabled={working}
                          onClick={() => accept(row)}
                        >
                          Accept
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
