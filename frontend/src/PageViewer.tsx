import { useState } from 'react'
import type { Page } from './api'

const MIN_ZOOM = 100 // percent of the half's width
const MAX_ZOOM = 500
const ZOOM_STEP = 50

interface Props {
  page: Page
  busy: boolean
  error: string | null
  onPrevious: () => void
  onNext: () => void
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: previous/next page buttons and the raw OCR text.
 */
export default function PageViewer({
  page,
  busy,
  error,
  onPrevious,
  onNext,
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)

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
      <section className="viewer-half">
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
        {error && <p role="alert">{error}</p>}
        <pre>{page.content}</pre>
      </section>
    </div>
  )
}
