import { useRef, useState, type ReactNode, type UIEvent } from 'react'
import { getSpellingSuggestions, type EditionMetadata, type Page } from './api'
import { editionSummary } from './editionLabel'
import type { EntityExample } from './EditionWideEntities'
import { entityKey, type Decision, type Entity } from './entities'
import PageText from './PageText'
import type { Word } from './segments'
import type { SectionKind, TextSelection } from './sections'
import Sidebar, { type SidebarTool, type SidebarView } from './Sidebar'
import StructureView from './StructureView'
import XmlView from './XmlView'

/** The arrow of a page button: backward (left) or forward (right). */
function Arrow({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {direction === 'left' ? (
        <path d="M19 12H5M11 6l-6 6 6 6" />
      ) : (
        <path d="M5 12h14M13 6l6 6-6 6" />
      )}
    </svg>
  )
}

/** The icon of the button that goes back to the editions: a house. */
function HomeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 11l9-8 9 8" />
      <path d="M5 10v10h5v-6h4v6h5V10" />
    </svg>
  )
}

/** The icon of the button that edits the metadata of the edition: a pencil. */
function EditIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 20l1-5L16.5 3.5a2.1 2.1 0 0 1 3 3L8 18z" />
      <path d="M14 6l4 4" />
    </svg>
  )
}

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
  /** The user wants the editions (the main page) back. */
  onBackToEditions: () => void
  /** What is known of the edition, shown above the page number; null if not known. */
  metadata: EditionMetadata | null
  /** Open the panel where the metadata of the edition is edited. */
  onEditMetadata: () => void
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
  /** The user double-clicked a place of the text and typed a token for it. */
  onAddToken: (position: number, token: string) => void
  /** Join two neighbouring words that the OCR split: the space between goes. */
  onJoinWords: (words: Word[]) => void
  /** Start a new line between two neighbouring words of a line. */
  onSplitLine: (words: Word[]) => void
  /** Remove the line break between two neighbouring words on two lines. */
  onJoinLines: (words: Word[]) => void
  /** A stretch of the text to show, by code point offsets (a place that is pointed at). */
  highlight: { start: number; end: number } | null
  /** A panel that is docked under the text (the missing hyphens), if any. */
  dock: ReactNode
  /** Replace a token, as it is wrong, with another, wherever it is in the edition. */
  onReplaceToken: () => void
  /**
   * Accept, as legal spellings, the flagged words of the whole edition that have a
   * substring (a core and its endings) in common.
   */
  onApplyRule: () => void
  /**
   * Look for a named entity that was confirmed on this page, in all its forms in
   * the cases, in the whole edition: `examples` are the confirmed entities.
   */
  onFindEntities: (examples: EntityExample[]) => void
  /** Look for the OCR rubbish (lines that are not text) in the whole edition. */
  onFindRubbish: () => void
  /** Look for the hyphens that the OCR dropped at the line ends of this page. */
  onFindHyphens: () => void
  /** Look for the page number that is marked on this page on the other pages (a
   * tool of the structure view). */
  onFindPageNumbers: () => void
  /** The user selected some text in the NER view and chose a label for it. */
  onCreateEntity: (selection: TextSelection, label: string) => void
  /** Mark the selected text of the page as a section of it. */
  onMarkSection: (
    selection: TextSelection,
    section: SectionKind,
    n?: string,
    lang?: string,
  ) => void
  /** What was done last, to tell the user (not an error). */
  notice: string | null
}

/**
 * Left half: the scan, with zoom buttons.
 * Right half: the buttons to go back to the editions and to the previous/next
 * page, and the page
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
  onBackToEditions,
  metadata,
  onEditMetadata,
  entities,
  recognizing,
  onRecognizeEntities,
  decisions,
  onConfirmEntity,
  onRejectEntity,
  onCorrectWord,
  onLegalSpelling,
  onMarkSection,
  onCreateEntity,
  onAddToken,
  onJoinWords,
  onSplitLine,
  onJoinLines,
  onFindPageNumbers,
  onFindHyphens,
  onFindRubbish,
  onFindEntities,
  onApplyRule,
  onReplaceToken,
  highlight,
  dock,
  notice,
}: Props) {
  const [zoom, setZoom] = useState(MIN_ZOOM)
  // Every page opens in the correction view: moving to another page goes back
  // to it, wherever the user was.
  const [view, setView] = useState<View>('correction')
  // The scans that have loaded, or failed to: until then (or for another page's
  // scan) a spinner is shown. Kept by address, so a new page starts loading.
  const [scan, setScan] = useState<{ src: string; failed: boolean } | null>(null)
  const scanDone = page.scan_uri !== null && scan?.src === page.scan_uri
  const scanLoading = page.scan_uri !== null && !scanDone
  const scanFailed = scanDone && scan.failed
  const [viewedPage, setViewedPage] = useState(page.number)
  if (viewedPage !== page.number) {
    setViewedPage(page.number)
    setView('correction')
  }

  // The scan and the text scroll together: scrolling one moves the other by the
  // same part of the way.
  const scanHalf = useRef<HTMLElement>(null)
  const textHalf = useRef<HTMLElement>(null)
  // The box that was just moved by the other one: its scroll event is the echo
  // of that, not the user, so it must not move the other back.
  const moved = useRef<HTMLElement | null>(null)
  function follow(from: HTMLElement, to: HTMLElement | null | undefined) {
    if (moved.current === from) {
      moved.current = null
      return
    }
    if (!to) return
    const fromRange = from.scrollHeight - from.clientHeight
    const toRange = to.scrollHeight - to.clientHeight
    if (fromRange <= 0 || toRange <= 0) return
    const before = to.scrollTop
    moved.current = to
    to.scrollTop = (from.scrollTop / fromRange) * toRange
    // no scroll event follows when it did not move: nothing to ignore then
    if (to.scrollTop === before) moved.current = null
  }
  const followScan = (event: UIEvent<HTMLElement>) =>
    follow(
      event.currentTarget,
      textHalf.current?.querySelector<HTMLElement>('.page-box'),
    )
  const followText = (event: UIEvent<HTMLElement>) => {
    const box = event.target as HTMLElement
    if (box.classList?.contains('page-box')) follow(box, scanHalf.current)
  }

  // A page number is marked on the page (told by the structure view).
  const [pageNumberMarked, setPageNumberMarked] = useState(false)

  // The named entities that were confirmed on this page: the examples that can be
  // looked for in the edition.
  const confirmed: EntityExample[] = (entities ?? []).flatMap((entity) => {
    const decision = decisions[entityKey(entity)]
    return decision?.kind === 'confirmed'
      ? [{ text: entity.text, label: decision.label }]
      : []
  })
  const examples = confirmed.filter(
    (example, index) =>
      confirmed.findIndex(
        (other) => other.text === example.text && other.label === example.label,
      ) === index,
  )

  // What the cog wheel of the sidebar offers: different tools in each view.
  const tools: SidebarTool[] =
    view === 'correction'
      ? [
          {
            label: 'Find missing hyphens',
            onSelect: onFindHyphens,
            disabled: busy || text.trim() === '',
            title:
              'Put back the hyphens that the OCR dropped at the end of the lines of this page',
          },
          {
            label: 'Apply spelling rule to the edition',
            onSelect: onApplyRule,
            disabled: busy,
            title:
              'Give a substring of letters, such as cy with the endings a, i, ami, and accept the flagged words of the whole edition that have it as legal spellings',
          },
          {
            label: 'Replace a token in the edition',
            onSelect: onReplaceToken,
            disabled: busy,
            title:
              'Give a token as it is wrong, such as tern, and the one to replace it with, such as tem, and replace it everywhere in the edition, place by place',
          },
          {
            label: 'Find OCR rubbish in the edition',
            onSelect: onFindRubbish,
            disabled: busy,
            title:
              'Find the lines of the whole edition that are not text (an ornament read as letters) and remove them',
          },
        ]
      : view === 'structure'
        ? [
            {
              label: 'Find this on other pages',
              onSelect: onFindPageNumbers,
              disabled: !pageNumberMarked || busy,
              title: pageNumberMarked
                ? 'Look for a page number like the one marked here on the other pages of the edition'
                : 'Mark the page number on this page first',
            },
          ]
        : view === 'ner'
          ? [
              {
                label: 'Find this NER on other pages',
                onSelect: () => onFindEntities(examples),
                disabled: busy || examples.length === 0,
                title:
                  examples.length > 0
                    ? 'Look for a named entity confirmed here, in all its forms in the cases, in the whole edition'
                    : 'Confirm a named entity on this page first',
              },
            ]
          : []

  return (
    <div className="viewer">
      <section className="viewer-half" ref={scanHalf} onScroll={followScan}>
        <div className="zoom-controls review-styled">
          <button
            type="button"
            className="review-round-button"
            aria-label="Zoom out"
            onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
            disabled={zoom <= MIN_ZOOM}
          >
            −
          </button>
          <span aria-live="polite">{zoom}%</span>
          <button
            type="button"
            className="review-round-button"
            aria-label="Zoom in"
            onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
            disabled={zoom >= MAX_ZOOM}
          >
            +
          </button>
        </div>
        {scanLoading && (
          <div
            className="scan-loading"
            role="progressbar"
            aria-label="Loading the scan"
          >
            <div className="scan-loading-place">
              <div className="scan-loading-box">
                <img
                  src="/skull-crossbones-loop.gif"
                  alt=""
                  width={44}
                  height={48}
                  className="scan-loading-image"
                />
                Loading the scan …
              </div>
            </div>
          </div>
        )}
        {scanFailed && <p role="alert">The scan could not be loaded.</p>}
        {page.scan_uri && (
          <img
            // (a new scan is a new image: it must not show the old one meanwhile)
            key={page.scan_uri}
            src={page.scan_uri}
            alt={`Scan of page ${page.number}`}
            style={{ width: `${zoom}%` }}
            hidden={scanFailed}
            // the scan is what the page is for: the browser must fetch it first
            fetchPriority="high"
            onLoad={() => setScan({ src: page.scan_uri!, failed: false })}
            onError={() => setScan({ src: page.scan_uri!, failed: true })}
          />
        )}
      </section>
      <section
        className="viewer-half text-half"
        ref={textHalf}
        // (scroll events do not bubble: the capture phase sees the text box's)
        onScrollCapture={followText}
      >
        <div className="edition-line">
          {metadata && (
            <span
              className="edition-line-text"
              title={[metadata.author, metadata.title].filter(Boolean).join('; ')}
            >
              {editionSummary(metadata)}
            </span>
          )}
          <button
            type="button"
            className="page-nav-button edit"
            aria-label="Edit the metadata of the edition"
            data-tooltip="Edit the metadata of the edition"
            onClick={onEditMetadata}
          >
            <EditIcon />
          </button>
        </div>
        <nav className="page-nav">
          <span className="page-nav-start">
            <button
              type="button"
              className="page-nav-button home"
              aria-label="Back to the list of editions"
              data-tooltip="Back to the list of editions"
              onClick={onBackToEditions}
            >
              <HomeIcon />
            </button>
            <button
              type="button"
              className="page-nav-button previous"
              aria-label="Previous page"
              data-tooltip="Previous page"
              onClick={onPrevious}
              disabled={page.number === 0 || busy}
            >
              <Arrow direction="left" />
            </button>
          </span>
          <span>Page {page.number}</span>
          <button
            type="button"
            className="page-nav-button next"
            aria-label="Next page"
            data-tooltip="Next page"
            onClick={onNext}
            disabled={busy}
          >
            <Arrow direction="right" />
          </button>
        </nav>
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
            // the closest replacements of a misspelled word, from the backend
            onSuggestSpelling={(word) =>
              getSpellingSuggestions(text, word, page.edition_id)
            }
            // in the NER view a selection can be made an entity of its own
            onCreateEntity={view === 'ner' ? onCreateEntity : undefined}
            // in the correction view a double click adds a token the OCR missed
            onAddToken={view === 'correction' ? onAddToken : undefined}
            // a page without text (not one whose XML could not be read) has
            // nothing to double-click: it gets a field for its first text
            emptyPage={text === '' && !textError}
            onJoinWords={view === 'correction' ? onJoinWords : undefined}
            onSplitLine={view === 'correction' ? onSplitLine : undefined}
            onJoinLines={view === 'correction' ? onJoinLines : undefined}
            highlight={view === 'correction' ? highlight : null}
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
            onPageNumberMarked={setPageNumberMarked}
          />
        )}
        {view === 'correction' && dock}
      </section>
      <Sidebar
        view={view}
        tools={tools}
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
