import { useState } from 'react'
import type { Page } from './api'
import Sidebar from './Sidebar'
import XmlView from './XmlView'

const MIN_ZOOM = 100 // percent of the half's width
const MAX_ZOOM = 500
const ZOOM_STEP = 50

type View = 'text' | 'xml'

interface Props {
  page: Page
  /** The page's text, as extracted from its TEI XML by the backend. */
  text: string
  /** Why `text` could not be extracted (the backend's reason), if so. */
  textError: string | null
  busy: boolean
  error: string | null
  onPrevious: () => void
  onNext: () => void
  /** Mark the page as ready. */
  onMarkReady: () => void
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: previous/next page and "Mark as ready" buttons, a switch between
 * the two views of the page, and the page itself, read-only: its text (the
 * default, as extracted by the backend) or the TEI XML it comes from, with the
 * markup on a grey background.
 */
export default function PageViewer({
  page,
  text,
  textError,
  busy,
  error,
  onPrevious,
  onNext,
  onMarkReady,
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)
  // Kept while moving from page to page.
  const [view, setView] = useState<View>('text')

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
            disabled={page.number === 0 || busy}
          >
            Previous page
          </button>
          <span>Page {page.number}</span>
          <button type="button" onClick={onNext} disabled={busy}>
            Next page
          </button>
        </nav>
        <div className="page-actions">
          <button
            type="button"
            onClick={onMarkReady}
            disabled={page.ready || busy}
          >
            {page.ready ? 'Ready' : 'Mark as ready'}
          </button>
          <div className="view-switch" role="group" aria-label="Show">
            {(['text', 'xml'] as const).map((choice) => (
              <button
                key={choice}
                type="button"
                aria-pressed={view === choice}
                onClick={() => setView(choice)}
              >
                {choice === 'text' ? 'Text' : 'XML'}
              </button>
            ))}
          </div>
        </div>
        {error && <p role="alert">{error}</p>}
        {textError && (
          <p role="alert">The text of this page cannot be shown: {textError}</p>
        )}
        {view === 'text' ? (
          <textarea aria-label="Page text" value={text} readOnly />
        ) : (
          <XmlView xml={page.content} />
        )}
      </section>
      <Sidebar />
    </div>
  )
}
