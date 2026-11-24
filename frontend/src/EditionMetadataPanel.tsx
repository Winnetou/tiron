import { useEffect, useState, type FormEvent } from 'react'
import {
  getEditionMetadata,
  updateEditionMetadata,
  type EditionMetadata,
} from './api'
import { Loading } from './Loading'

type Loaded =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; saved: EditionMetadata }

/** The four fields as the user writes them: text, the year too. */
interface Fields {
  author: string
  title: string
  place: string
  year: string
}

const fieldsOf = (metadata: EditionMetadata): Fields => ({
  author: metadata.author ?? '',
  title: metadata.title ?? '',
  place: metadata.place ?? '',
  year: metadata.year === null ? '' : String(metadata.year),
})

const FIRST_YEAR = 1500
const LAST_YEAR = 2030
/** The year that the list is opened at when none is known: the likeliest one. */
const LIKELY_YEAR = 1900
const YEARS = Array.from({ length: LAST_YEAR - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i)

interface Props {
  editionId: string
  /** Told what is known of the edition once it was saved (for what shows it). */
  onSaved?: (metadata: EditionMetadata) => void
  onClose: () => void
}

/**
 * The panel where what is known of the edition is edited, for when what was fetched
 * from Polona is not complete (`GET` and `PATCH /editions/{id}/metadata`). It has two
 * parts: the work, its author and title (the work is the book, and the editions that
 * share it have the change too, which the panel says), and the edition itself, its
 * place and year. Save sends the fields that were changed; an empty one clears it.
 * The backend is the judge of what is valid (the year is picked from 1500 to 2030, and the list opens at 1900 when none is known): its reason
 * is shown if it refuses.
 */
export default function EditionMetadataPanel({
  editionId,
  onSaved,
  onClose,
}: Props) {
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' })
  const [fields, setFields] = useState<Fields>({
    author: '',
    title: '',
    place: '',
    year: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    let current = true
    getEditionMetadata(editionId)
      .then((metadata) => {
        if (!current) return
        setFields(fieldsOf(metadata))
        setLoaded({ kind: 'done', saved: metadata })
      })
      .catch(
        (reason: unknown) =>
          current &&
          setLoaded({
            kind: 'error',
            message: reason instanceof Error ? reason.message : 'Request failed',
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

  function change(name: keyof Fields, value: string) {
    setFields((now) => ({ ...now, [name]: value }))
    setDone(false)
  }

  const saved = loaded.kind === 'done' ? fieldsOf(loaded.saved) : null
  const changedNames =
    saved === null
      ? []
      : (Object.keys(fields) as (keyof Fields)[]).filter(
          (name) => fields[name].trim() !== saved[name],
        )
  const changed = changedNames.length > 0

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!changed || busy) return
    setBusy(true)
    setError(null)
    try {
      // only what was changed: the others are left as they are
      const sent: Parameters<typeof updateEditionMetadata>[1] = {}
      for (const name of changedNames) {
        const text = fields[name].trim()
        if (name === 'year') sent.year = text === '' ? null : Number(text)
        else sent[name] = text || null
      }
      const metadata = await updateEditionMetadata(editionId, sent)
      setFields(fieldsOf(metadata))
      setLoaded({ kind: 'done', saved: metadata })
      setDone(true)
      onSaved?.(metadata)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  const others = loaded.kind === 'done' ? loaded.saved.work_editions - 1 : 0

  return (
    <div className="review-backdrop">
      <form
        role="dialog"
        aria-modal="true"
        aria-label="Edition metadata"
        className="review-box review-styled metadata-box"
        onSubmit={save}
      >
        <header className="review-header">
          <h2>Edition metadata</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          {loaded.kind === 'loading' && <Loading>Loading the metadata …</Loading>}
          {loaded.kind === 'error' && <p role="alert">{loaded.message}</p>}
          {loaded.kind === 'done' && (
            <>
              <p className="structure-hint">
                The metadata fetched from Polona is not always complete. An empty field
                is cleared.
              </p>
              <fieldset className="metadata-group">
                <legend>Work</legend>
                <p className="structure-hint">
                  The author as “Last name, First names (years)”.{' '}
                  {others > 0
                    ? `The work has ${others} other ${others === 1 ? 'edition' : 'editions'}, which are changed too.`
                    : 'No other edition has this work.'}
                </p>
                <label className="metadata-field">
                  Author
                  <input
                    className="styled-input"
                    value={fields.author}
                    maxLength={300}
                    disabled={busy}
                    onChange={(event) => change('author', event.target.value)}
                  />
                </label>
                <label className="metadata-field">
                  Title
                  <input
                    className="styled-input"
                    value={fields.title}
                    maxLength={1000}
                    disabled={busy}
                    onChange={(event) => change('title', event.target.value)}
                  />
                </label>
              </fieldset>
              <fieldset className="metadata-group">
                <legend>This edition</legend>
                <label className="metadata-field">
                  Place
                  <input
                    className="styled-input"
                    value={fields.place}
                    maxLength={300}
                    disabled={busy}
                    onChange={(event) => change('place', event.target.value)}
                  />
                </label>
                <label className="metadata-field">
                  Year
                  <select
                    className="styled-input"
                    value={fields.year}
                    disabled={busy}
                    // a year that is not known opens the list at the likeliest one
                    onMouseDown={() => {
                      if (fields.year === '') change('year', String(LIKELY_YEAR))
                    }}
                    onChange={(event) => change('year', event.target.value)}
                  >
                    <option value="">—</option>
                    {YEARS.map((year) => (
                      <option key={year} value={year}>
                        {year}
                      </option>
                    ))}
                  </select>
                </label>
              </fieldset>
              {error && (
                <p role="alert" className="review-error">
                  {error}
                </p>
              )}
              {done && <p role="status">✓ Saved</p>}
              <div className="metadata-actions">
                <button type="submit" disabled={!changed || busy}>
                  Save
                </button>
              </div>
            </>
          )}
        </div>
      </form>
    </div>
  )
}
