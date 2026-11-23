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

/** A year is a whole number (the backend takes 1500 to 2030), or nothing. */
const isYear = (text: string) => text.trim() === '' || /^\d{1,4}$/.test(text.trim())

interface Props {
  editionId: string
  onClose: () => void
}

/**
 * The panel where what is known of the edition is edited, for when what was fetched
 * from Polona is not complete (`GET` and `PATCH /editions/{id}/metadata`). It has two
 * parts: the work, its author and title (the work is the book, and the editions that
 * share it have the change too, which the panel says), and the edition itself, its
 * place and year. Save sends the fields that were changed; an empty one clears it.
 * The backend is the judge of what is valid (the year from 1500 to 2030): its reason
 * is shown if it refuses.
 */
export default function EditionMetadataPanel({ editionId, onClose }: Props) {
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
  const valid = isYear(fields.year)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!changed || !valid || busy) return
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
                  <input
                    className="styled-input"
                    value={fields.year}
                    inputMode="numeric"
                    maxLength={4}
                    size={6}
                    disabled={busy}
                    aria-invalid={!valid}
                    onChange={(event) => change('year', event.target.value)}
                  />
                </label>
              </fieldset>
              {!valid && (
                <p role="alert" className="review-error">
                  The year is a number, like 1890.
                </p>
              )}
              {error && (
                <p role="alert" className="review-error">
                  {error}
                </p>
              )}
              {done && <p role="status">✓ Saved</p>}
              <div className="metadata-actions">
                <button type="submit" disabled={!changed || !valid || busy}>
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
