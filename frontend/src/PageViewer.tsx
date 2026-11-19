import { useState } from 'react'
import type { Page } from './api'
import type { Decision, Entity } from './entities'
import PageText from './PageText'
import type { Word } from './segments'
import type { SectionKind, TextSelection } from './sections'
import Sidebar, { type SidebarView } from './Sidebar'
import StructureView from './StructureView'
import XmlView from './XmlView'

const MIN_ZOOM = 100 // percent of the half's width
const MAX_ZOOM = 500
const ZOOM_STEP = 50

type View = SidebarView

interface Props {
  page: Page
  /** The page's text, as extracted from its TEI XML by the backend. */
  text: string
  /** Why `text` could not be extracted (the backend's reason), if so. */
  textError: string | null
  /** Every word of `text` with its spelling verdict; null if not checked. */
  words: Word[] | null
  /** Why the spelling could not be checked (the backend's reason), if so. */
  spellError: string | null
  busy: boolean
  error: string | null
  onPrevious: () => void
  onNext: () => void
  /** Mark the page as ready. */
  onMarkReady: () => void
  /** The named entities found in `text`; null if not recognized yet. */
  entities: Entity[] | null
  recognizing: boolean
  /** Recognize the named entities in `text`. */
  onRecognizeEntities: () => void
  /** What was decided about the entities so far (confirmed or rejected). */
  decisions: Record<string, Decision>
  /** Mark the entity in the XML of the page, as `label`. */
  onConfirmEntity: (entity: Entity, label: string) => void
  /** The user says it is not a named entity: it is no longer highlighted. */
  onRejectEntity: (entity: Entity) => void
  /** The user corrected a word of the text: it becomes `after`. */
  onCorrectWord: (word: Word, after: string) => void
  /** The user says the spelling of a misspelled word is legal in the edition. */
  onLegalSpelling: (word: Word) => void
  /** Mark the selected text of the page as a section of it. */
  onMarkSection: (
    selection: TextSelection,
    section: SectionKind,
    n?: string,
  ) => void
  /** What was done last, to tell the user (not an error). */
  notice: string | null
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: previous/next page and "Mark as ready" buttons, and the page
 * itself, read-only: its text as extracted by the backend, in which the
 * spelling is corrected, or (NER view) the named entities, recognized by the
 * backend. The Text button shows the text as it is, the XML button the page's
 * XML (`xml_content_corrected`) with the markup on a yellow background. The Structure button of the sidebar shows the text in a way that
 * the sections of the page can be marked in it.
 */
export default function PageViewer({
  page,
  text,
  textError,
  words,
  spellError,
  busy,
  error,
  onPrevious,
  onNext,
  onMarkReady,
  entities,
  recognizing,
  onRecognizeEntities,
  decisions,
  onConfirmEntity,
  onRejectEntity,
  onCorrectWord,
  onLegalSpelling,
  onMarkSection,
  notice,
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)
  const misspelled = (words ?? []).filter((word) => !word.correct).length
  // Every page opens in the correction view: moving to another page goes back
  // to it, wherever the user was.
  const [view, setView] = useState<View>('correction')
  const [viewedPage, setViewedPage] = useState(page.number)
  if (viewedPage !== page.number) {
    setViewedPage(page.number)
    setView('correction')
  }

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
            // a page with a misspelled word (in red) cannot be marked as ready
            disabled={page.ready || busy || misspelled > 0}
            title={
              misspelled > 0 && !page.ready
                ? 'Correct the misspelled words first'
                : undefined
            }
          >
            {page.ready ? 'Ready' : 'Mark as ready'}
          </button>
        </div>
        {error && <p role="alert">{error}</p>}
        {spellError && (
          <p role="alert">The spelling cannot be checked: {spellError}</p>
        )}
        {notice && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
        {textError && (
          <p role="alert">The text of this page cannot be shown: {textError}</p>
        )}
        {/* The correction view is where the spelling is corrected: the entities are not
            shown in it, and the NER view does not show the spelling. */}
        {(view === 'correction' || view === 'ner') && (
          <PageText
            text={text}
            words={view === 'correction' ? words : null}
            entities={view === 'ner' ? entities : null}
            decisions={decisions}
            busy={busy}
            onConfirm={onConfirmEntity}
            onReject={onRejectEntity}
            onCorrectWord={onCorrectWord}
            onLegalSpelling={onLegalSpelling}
          />
        )}
        {view === 'text' && (
          <PageText
            text={text}
            words={null}
            entities={null}
            decisions={decisions}
            busy={busy}
            onConfirm={onConfirmEntity}
            onReject={onRejectEntity}
            onCorrectWord={onCorrectWord}
            onLegalSpelling={onLegalSpelling}
          />
        )}
        {view === 'xml' && <XmlView xml={page.content} />}
        {view === 'structure' && (
          <StructureView
            text={text}
            xml={page.content}
            busy={busy}
            onMark={onMarkSection}
          />
        )}
      </section>
      <Sidebar
        view={view}
        onShow={setView}
        canRecognize={text.trim() !== ''}
        recognizing={recognizing}
        onRecognizeEntities={() => {
          setView('ner')
          onRecognizeEntities()
        }}
      />
    </div>
  )
}
