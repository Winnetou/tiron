import { useState } from 'react'
import type { Page } from './api'
import Sidebar from './Sidebar'

const MIN_ZOOM = 100 // percent of the half's width
const MAX_ZOOM = 500
const ZOOM_STEP = 50

interface Props {
  page: Page
  busy: boolean
  error: string | null
  onPrevious: () => void
  onNext: () => void
  /** Store `content` as the page's corrected content. */
  onSave: (content: string) => void
  /** Mark the page as ready. */
  onMarkReady: () => void
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: previous/next page, Save and "Mark as ready" buttons, and the
 * page text, which can be edited.
 */
export default function PageViewer({
  page,
  busy,
  error,
  onPrevious,
  onNext,
  onSave,
  onMarkReady,
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)

  // The text being edited. It starts over from the page's content whenever a
  // different page (or a saved version of it) arrives. Marking the page as
  // ready changes only the flag, so an unsaved edit survives it.
  const [draft, setDraft] = useState(page.content)
  const [shownPage, setShownPage] = useState(page)
  if (page !== shownPage) {
    setShownPage(page)
    const onlyReadyChanged =
      page.id === shownPage.id &&
      page.content === shownPage.content &&
      page.ready !== shownPage.ready
    if (!onlyReadyChanged) setDraft(page.content)
  }
  const dirty = draft !== page.content

  return (
    <div className="viewer">
      <section className="viewer-half">
        <div className="zoom-controls">
          <button
            type="button"
            aria-label="Zoom out"
            onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
            disabled={zoom <= MIN_ZOOM}
          >
            −
          </button>
          <span aria-live="polite">{zoom}%</span>
          <button
            type="button"
            aria-label="Zoom in"
            onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
            disabled={zoom >= MAX_ZOOM}
          >
            +
          </button>
        </div>
        {page.scan_uri && (
          <img
            src={page.scan_uri}
            alt={`Scan of page ${page.number}`}
            style={{ width: `${zoom}%` }}
          />
        )}
      </section>
      <section className="viewer-half text-half">
        <nav className="page-nav">
          <button
            type="button"
            onClick={onPrevious}
            disabled={page.number === 0 || busy || dirty}
          >
            Previous page
          </button>
          <span>Page {page.number}</span>
          <button type="button" onClick={onNext} disabled={busy || dirty}>
            Next page
          </button>
        </nav>
        <div className="page-actions">
          <button
            type="button"
            onClick={() => onSave(draft)}
            disabled={!dirty || busy}
          >
            Save
          </button>
          <button
            type="button"
            onClick={onMarkReady}
            disabled={page.ready || busy}
          >
            {page.ready ? 'Ready' : 'Mark as ready'}
          </button>
          {dirty && <span className="unsaved">Unsaved changes</span>}
        </div>
        {error && <p role="alert">{error}</p>}
        <textarea
          aria-label="Page text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          spellCheck={false}
        />
      </section>
      <Sidebar />
    </div>
  )
}
