import { useState } from 'react'
import type { Page } from './api'
import { entityKey, type Decision, type Entity } from './entities'
import PageText from './PageText'
import type { Word } from './segments'
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
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: previous/next page and "Mark as ready" buttons, a switch between
 * the two views of the page, and the page itself, read-only: its text (the
 * default, as extracted by the backend, with the named entities in red once
 * they are recognized) or the TEI XML it comes from, with the markup on a grey
 * background.
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
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)
  // The entities that are still shown: not the ones rejected as "Not NER".
  const misspelled = (words ?? []).filter((word) => !word.correct).length
  const entityCount = (entities ?? []).filter(
    (entity) => decisions[entityKey(entity)]?.kind !== 'rejected',
  ).length
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
          {words && (
            <span className="spell-count" aria-live="polite">
              {misspelled === 0 ? 'No misspelled words' : `${misspelled} misspelled`}
            </span>
          )}
          {entities && (
            <span className="entity-count" aria-live="polite">
              {entityCount === 0
                ? 'No entities'
                : `${entityCount} ${entityCount === 1 ? 'entity' : 'entities'}`}
            </span>
          )}
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
        {spellError && (
          <p role="alert">The spelling cannot be checked: {spellError}</p>
        )}
        {textError && (
          <p role="alert">The text of this page cannot be shown: {textError}</p>
        )}
        {view === 'text' ? (
          <PageText
            text={text}
            words={words}
            entities={entities}
            decisions={decisions}
            busy={busy}
            onConfirm={onConfirmEntity}
            onReject={onRejectEntity}
            onCorrectWord={onCorrectWord}
            onLegalSpelling={onLegalSpelling}
          />
        ) : (
          <XmlView xml={page.content} />
        )}
      </section>
      <Sidebar
        canRecognize={text.trim() !== ''}
        recognizing={recognizing}
        onRecognizeEntities={() => {
          setView('text') // the entities are shown in the text
          onRecognizeEntities()
        }}
      />
    </div>
  )
}
