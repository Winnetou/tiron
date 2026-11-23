import { useEffect, useState } from 'react'
import {
  getSimilarEntities,
  type EntityForm,
  type EntityMatch,
  type SimilarEntities,
} from './api'
import { Loading } from './Loading'

/** A named entity that was confirmed on the page: the example to look for. */
export interface EntityExample {
  text: string
  /** The label it was confirmed with (`persName`, `placeName`, ...). */
  label: string
}

/** One place that has a form of the entity, and what became of it. */
interface Row {
  /** A number of its own. */
  id: number
  page: number
  match: EntityMatch
  /** Is it among the places that "Approve all" takes? */
  include: boolean
  state: 'waiting' | 'working' | 'approved' | 'failed'
  error?: string
}

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; found: SimilarEntities; rows: Row[] }

/** The ones that the model also takes for an entity are taken by default. */
function rowsOf(found: SimilarEntities): Row[] {
  let id = 0
  return found.pages.flatMap(({ page_number, matches }) =>
    matches.map((match) => ({
      id: id++,
      page: page_number,
      match,
      include: match.sure,
      state: 'waiting' as const,
    })),
  )
}

/** A match over two lines is one form with a space in it. */
const formOf = (match: EntityMatch) => match.text.replace(/\s+/g, ' ')

const caseNames: Record<string, string> = {
  nom: 'nominative',
  gen: 'genitive',
  dat: 'dative',
  acc: 'accusative',
  ins: 'instrumental',
  loc: 'locative',
  voc: 'vocative',
}

/** "gen.sg" as "genitive sg". */
const caseName = (name: string) => {
  const [grammatical, number] = name.split('.')
  return number ? `${caseNames[grammatical] ?? grammatical} ${number}` : name
}

const describe = (form: EntityForm) =>
  form.cases.filter((c) => c !== 'any').map(caseName).join(', ')

interface Props {
  editionId: string
  /** The named entities confirmed on the page: one of them is looked for. */
  examples: EntityExample[]
  /**
   * Mark the entity at a place of a page, with the label. Rejects with the reason
   * if the backend refuses.
   */
  onApprove: (page: number, match: EntityMatch, label: string) => Promise<void>
  onClose: () => void
}

/**
 * The big box with the places of the edition that have a named entity that was
 * confirmed on the page, in any of its forms in the cases (`GET
 * /editions/{id}/similar-entities`): a Polish name is not always in the
 * nominative, "Machnicki" is also "Machnickiego" and "Machnickiemu". The user
 * picks which of the confirmed entities to look for. The forms that were made are
 * listed, each with a box to drop it. Each place is shown in its context, the
 * nearest word before it (with the signs between), the entity (marked) and the
 * nearest word after it, so that it can be judged without the page. Each has an Approve button, which marks the entity at
 * that place alone (it carries the edition, the page and the offsets), and a box to
 * take it out of "Approve all". Those that the model does not also take for an
 * entity are out by default. Nothing is marked until the user approves; marking
 * does not change the text, so the offsets stay.
 */
export function SimilarEntitiesReview({
  editionId,
  examples,
  onApprove,
  onClose,
}: Props) {
  const [chosen, setChosen] = useState(0)
  const example = examples[Math.min(chosen, examples.length - 1)]
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })
  // The forms that were dropped by the user.
  const [dropped, setDropped] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  )

  useEffect(() => {
    let current = true
    getSimilarEntities(editionId, example.text, example.label)
      .then((found) => {
        if (!current) return
        setDropped(new Set())
        setLoaded({ kind: 'done', found, rows: rowsOf(found) })
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
  }, [editionId, example.text, example.label])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  function update(change: (rows: Row[]) => Row[]) {
    setLoaded((now) =>
      now.kind === 'done' ? { ...now, rows: change(now.rows) } : now,
    )
  }

  async function approve(row: Row): Promise<boolean> {
    update((all) =>
      all.map((r) =>
        r.id === row.id ? { ...r, state: 'working', error: undefined } : r,
      ),
    )
    try {
      await onApprove(row.page, row.match, example.label)
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
      all.map((r) => (r.id === row.id ? { ...r, state: 'approved' } : r)),
    )
    return true
  }

  const all = loaded.kind === 'done' ? loaded.rows : []
  // a form that was dropped hides its places, and they are not approved
  const rows = all.filter((row) => !dropped.has(formOf(row.match)))
  const working = all.some((row) => row.state === 'working') || progress !== null
  const todo = rows.filter((row) => row.include && row.state !== 'approved')
  const pages = [...new Set(rows.map((row) => row.page))]

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
    update((now) => now.map((row) => ({ ...row, include: change(row) })))
  }

  function toggleForm(form: string, on: boolean) {
    setDropped((now) => {
      const next = new Set(now)
      if (on) next.delete(form)
      else next.add(form)
      return next
    })
  }

  const sure = rows.filter((row) => row.match.sure).length
  const approved = rows.filter((row) => row.state === 'approved').length
  const failed = rows.filter((row) => row.state === 'failed').length
  const found = loaded.kind === 'done' ? loaded.found : null
  const present = new Set(all.map((row) => formOf(row.match)))

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="The same named entity in the edition"
        className="review-box review-styled"
      >
        <header className="review-header">
          <h2>
            The same named entity in the edition: <strong>{example.text}</strong>{' '}
            ({example.label})
          </h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          {examples.length > 1 && (
            <p>
              <label>
                Look for{' '}
                <select
                  aria-label="Named entity"
                  value={Math.min(chosen, examples.length - 1)}
                  disabled={working}
                  onChange={(event) => {
                    setLoaded({ kind: 'loading' })
                    setChosen(Number(event.target.value))
                  }}
                >
                  {examples.map((candidate, index) => (
                    <option key={`${candidate.text}/${candidate.label}`} value={index}>
                      {candidate.text} ({candidate.label})
                    </option>
                  ))}
                </select>
              </label>
            </p>
          )}
          {loaded.kind === 'loading' && (
            <Loading>Looking for the forms of the name in the edition …</Loading>
          )}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {found && (
            <details className="review-forms">
              <summary>
                Forms that were looked for ({found.forms.length}), {present.size} of
                them found
              </summary>
              <ul className="review-form-list">
                {found.forms.map((form) => (
                  <li key={form.text}>
                    <label>
                      <input
                        type="checkbox"
                        aria-label={`Form ${form.text}`}
                        checked={!dropped.has(form.text)}
                        disabled={working}
                        onChange={(event) => toggleForm(form.text, event.target.checked)}
                      />{' '}
                      <span className={present.has(form.text) ? 'suggestion' : ''}>
                        {form.text}
                      </span>{' '}
                      <span className="review-context">{describe(form)}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {found && rows.length === 0 && (
            <p role="status">No other place in the edition has this name.</p>
          )}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? 'place' : 'places'} on {pages.length}{' '}
                {pages.length === 1 ? 'page' : 'pages'}, {sure} of them sure
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
                  onClick={() => include((row) => row.match.sure)}
                >
                  Include only the sure ones
                </button>
                {progress && (
                  <span role="status">
                    Approving {progress.done} of {progress.total} …
                  </span>
                )}
              </div>
              {pages.map((page) => (
                <section key={page} aria-label={`Page ${page}`}>
                  <h3>Page {page}</h3>
                  <ul className="review-list">
                    {rows
                      .filter((row) => row.page === page)
                      .map((row) => {
                        const { match } = row
                        return (
                          <li
                            key={row.id}
                            className={`review-row ${row.state}${match.sure ? '' : ' misfit'}`}
                          >
                            <input
                              type="checkbox"
                              aria-label={`Include ${match.text} on page ${page} in Approve all`}
                              checked={row.include}
                              disabled={working || row.state === 'approved'}
                              onChange={(event) =>
                                update((now) =>
                                  now.map((r) =>
                                    r.id === row.id
                                      ? { ...r, include: event.target.checked }
                                      : r,
                                  ),
                                )
                              }
                            />
                            <span className="review-trigram">
                              <span className="review-context">
                                {match.before}
                              </span>{' '}
                              <mark>{match.text}</mark>{' '}
                              <span className="review-context">{match.after}</span>
                            </span>
                            {!match.sure && (
                              <span className="review-misfit-note">
                                the model does not take it for this entity
                              </span>
                            )}
                            {row.state === 'approved' ? (
                              <span className="review-done">✓ Approved</span>
                            ) : (
                              <button
                                type="button"
                                aria-label={`Approve ${match.text} on page ${page}`}
                                data-edition-id={editionId}
                                data-page={page}
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
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
