import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from 'react'
import {
  ENTITY_LABELS,
  TEXT_LABELS,
  entityKey,
  labelOptions,
  type Decision,
  type Entity,
} from './entities'
import { caretAt, caretInText, selectionInText } from './selection'
import { buildSegments, type Segment, type Word } from './segments'
import type { TextSelection } from './sections'

const NOT_NER = 'Not NER'

/** About how wide the dialog of a new entity is, to keep it inside the box. */
const POPUP_WIDTH = 300

/** The dialog to add a token: where it is, in the box, and for which text. */
interface TokenPending {
  /** The place in the text, between two characters, in code points. */
  position: number
  forText: string
  left: number
  placement: { top: number } | { bottom: number }
}

/** The dialog of a selection: where it is, in the box, and for which text. */
interface Pending {
  selection: TextSelection
  /** The text the selection was made in: it is only valid for that text. */
  forText: string
  left: number
  /** Below the selection, or above it for one in the lower half of the box. */
  placement: { top: number } | { bottom: number }
}

interface EntityMarkProps {
  entity: Entity
  decision: Decision | undefined
  busy: boolean
  /** True while a word inside the entity is being edited. */
  editing: boolean
  children: ReactNode
  onConfirm: (entity: Entity, label: string) => void
  onReject: (entity: Entity) => void
}

/**
 * A recognized entity, in red. While the mouse is over it (or it has the
 * keyboard focus) a small dialog offers all the labels, with the recognized one
 * selected, in case the recognition is wrong, and "Not NER", to be chosen and
 * confirmed. An entity that was confirmed is shown in green and has no dialog.
 */
function EntityMark({
  entity,
  decision,
  busy,
  editing,
  children,
  onConfirm,
  onReject,
}: EntityMarkProps) {
  const [open, setOpen] = useState(false)
  // In the lower half of the page the dialog goes above the entity: below it
  // it would be out of sight.
  const [above, setAbove] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)
  const [choice, setChoice] = useState(entity.label)
  const confirmed = decision?.kind === 'confirmed'
  const group = `entity-${entity.start}-${entity.end}`

  function confirm() {
    setOpen(false)
    if (choice === NOT_NER) onReject(entity)
    else onConfirm(entity, choice)
  }

  /** Show the dialog (unless there is nothing to choose), placed by where the entity is. */
  function show() {
    const show = !confirmed && !editing
    if (show && wrap.current) {
      const page = wrap.current.closest('.page-box')?.getBoundingClientRect()
      const mark = wrap.current.getBoundingClientRect()
      // (jsdom, the tests, has no layout: everything is at 0, so below)
      setAbove(!!page && mark.bottom - page.top > page.height / 2)
    }
    setOpen(show)
  }

  return (
    <span
      className="entity-wrap"
      ref={wrap}
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
      onFocus={show}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false)
      }}
    >
      <mark
        className={confirmed ? 'entity confirmed' : 'entity'}
        title={confirmed ? `${decision.label}, marked in the XML` : undefined}
        tabIndex={confirmed ? undefined : 0}
        // clicking a word in it starts editing the word: no dialog over it
        onClick={(event) => {
          if ((event.target as Element).closest('.word')) setOpen(false)
        }}
      >
        {children}
      </mark>
      {open && !editing && (
        <div
          role="dialog"
          aria-label={`Named entity: ${entity.text}`}
          className={above ? 'entity-dialog above' : 'entity-dialog'}
        >
          {[...labelOptions(entity), NOT_NER].map((option) => (
            <label key={option}>
              <input
                type="radio"
                name={group}
                value={option}
                checked={choice === option}
                onChange={() => setChoice(option)}
              />
              {option}
            </label>
          ))}
          <button type="button" onClick={confirm} disabled={busy}>
            Confirm
          </button>
        </div>
      )}
    </span>
  )
}

interface WordEditorProps {
  word: Word
  busy: boolean
  onSubmit: (after: string) => void
  /** Join the word with the previous / next one (only if there is a place). */
  onJoinPrevious?: () => void
  onJoinNext?: () => void
  /** The spelling is legal in this edition (only offered for a misspelled word). */
  onLegal: () => void
  onCancel: () => void
}

/**
 * The word as a text field, to be edited in place. Enter confirms and Escape
 * (or leaving the field) cancels. Nothing is sent if the text is empty or is
 * the same as before. A misspelled word also has a "Legal spelling" button next
 * to the field, to say that the spelling as it is, is legal in the edition.
 * Next to a word that has another one right before or after it, with only a
 * space between, "Join with previous" / "Join with next" remove that space (the
 * OCR split a word in two). While a request is running the field is disabled
 * and cannot be left.
 */
function WordEditor({
  word,
  busy,
  onSubmit,
  onJoinPrevious,
  onJoinNext,
  onLegal,
  onCancel,
}: WordEditorProps) {
  const [value, setValue] = useState(word.text)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])

  function submit() {
    const after = value.trim()
    if (after === word.text) onCancel()
    // an empty field removes the token: the backend replaces it with nothing
    else onSubmit(after)
  }

  return (
    <span
      className="word-editor"
      // Leaving the field and the button cancels. The field is disabled while a
      // request runs, which can blur it: that must not cancel what is being saved.
      onBlur={(event) => {
        if (!busy && !event.currentTarget.contains(event.relatedTarget)) onCancel()
      }}
    >
      <input
        ref={input}
        className="word-input"
        aria-label={`Correct "${word.text}"`}
        title="Leave it empty to remove it"
        value={value}
        size={Math.max(value.length, 2)}
        disabled={busy}
        spellCheck={false}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            submit()
          } else if (event.key === 'Escape') {
            event.stopPropagation()
            onCancel()
          }
        }}
      />
      {!word.correct && (
        <button
          type="button"
          className="legal-button"
          title={`"${word.text}" is a legal spelling in this edition`}
          disabled={busy}
          // keep the focus in the field, so clicking does not blur (cancel) it
          onMouseDown={(event) => event.preventDefault()}
          onClick={onLegal}
        >
          Legal spelling
        </button>
      )}
      {onJoinPrevious && (
        <button
          type="button"
          className="join-button"
          title="Remove the space before the word"
          disabled={busy}
          onMouseDown={(event) => event.preventDefault()}
          onClick={onJoinPrevious}
        >
          ← Join with previous
        </button>
      )}
      {onJoinNext && (
        <button
          type="button"
          className="join-button"
          title="Remove the space after the word"
          disabled={busy}
          onMouseDown={(event) => event.preventDefault()}
          onClick={onJoinNext}
        >
          Join with next →
        </button>
      )}
    </span>
  )
}

interface Props {
  text: string
  /** Every word of the text, with its verdict; null if not known. */
  words: Word[] | null
  /** Null until the entities have been recognized. */
  entities: Entity[] | null
  /** What was decided about the entities so far, by entityKey. */
  decisions: Record<string, Decision>
  /** True while a request is running: nothing can be confirmed meanwhile. */
  busy: boolean
  onConfirm: (entity: Entity, label: string) => void
  onReject: (entity: Entity) => void
  /** The user corrected a word: it becomes `after`. */
  onCorrectWord: (word: Word, after: string) => void
  /** The user says the spelling of a misspelled word is legal in the edition. */
  onLegalSpelling: (word: Word) => void
  /**
   * The user selected some text and chose a label for it: an entity that the
   * recognition did not find. Without it (outside the NER view) a selection
   * does nothing.
   */
  onCreateEntity?: (selection: TextSelection, label: string) => void
  /**
   * The user double-clicked a place of the text and typed a token that the OCR
   * missed: it goes there (`position` is between two characters, in code
   * points). Without it (outside the correction view) a double click does
   * nothing.
   */
  onAddToken?: (position: number, token: string) => void
  /**
   * The user chose to join a word with its neighbour, two words that the OCR
   * split: the space between them goes. Without it (outside the correction
   * view) the words cannot be joined.
   */
  onJoinWords?: (words: Word[]) => void
}

/**
 * The text of a page. Misspelled words are in red; any word can be clicked to
 * correct it in place, and a misspelled one can also be declared a legal
 * spelling. The recognized entities are marked in red as well (the
 * ones rejected as "Not NER" are not marked any more).
 */
export default function PageText({
  text,
  words,
  entities,
  decisions,
  busy,
  onConfirm,
  onReject,
  onCorrectWord,
  onLegalSpelling,
  onCreateEntity,
  onAddToken,
  onJoinWords,
}: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLPreElement>(null)
  // The selection that a new entity can be made of, and the label chosen for it.
  const [pending, setPending] = useState<Pending | null>(null)
  const [label, setLabel] = useState('')
  const active = pending && pending.forText === text ? pending : null

  // The place where a token is being added, and what was typed for it.
  const [tokenAt, setTokenAt] = useState<TokenPending | null>(null)
  const [token, setToken] = useState('')
  const addingAt = tokenAt && tokenAt.forText === text ? tokenAt : null

  function clear() {
    setPending(null)
    setLabel('')
    setTokenAt(null)
    setToken('')
  }

  // Escape closes the dialogs wherever the keyboard focus is.
  const open = active !== null || addingAt !== null
  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        clear()
        window.getSelection()?.removeAllRanges()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  /**
   * A double click on a place of the text (not on a word, which is for editing
   * it): the dialog to add a token there appears next to the click.
   */
  function doubleClicked(event: MouseEvent<HTMLElement>) {
    if (!onAddToken || !wrap.current || !box.current) return
    if ((event.target as Element).closest('.word, [role="dialog"]')) return
    const caret = caretAt(event.clientX, event.clientY)
    const position = caret && caretInText(box.current, caret.node, caret.offset, text)
    // the double click selected the word under it: that is not wanted
    window.getSelection()?.removeAllRanges()
    if (position === null || position === undefined) return clear()

    const area = wrap.current.getBoundingClientRect()
    const lower = event.clientY - area.top > area.height / 2
    setPending(null)
    setTokenAt({
      position,
      forText: text,
      left: Math.max(0, Math.min(event.clientX - area.left, area.width - POPUP_WIDTH)),
      placement: lower
        ? { bottom: Math.max(0, area.bottom - event.clientY + 6) }
        : { top: event.clientY - area.top + 6 },
    })
    setToken('')
  }

  function add() {
    // several words are fine: a run of spaces is one
    const typed = token.trim().replace(/\s+/g, ' ')
    if (!addingAt || !onAddToken || typed === '') return
    onAddToken(addingAt.position, typed)
    clear()
  }

  /** Called when a selection may have been made: show the dialog for it. */
  function read() {
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
      return clear()
    }
    const range = selection.getRangeAt(0)
    const found = box.current && selectionInText(box.current, range, text)
    if (!found || !wrap.current) return clear()

    // jsdom (the tests) has no layout: then the dialog is at the corner
    const rect = range.getBoundingClientRect?.() ?? new DOMRect()
    const area = wrap.current.getBoundingClientRect()
    const lower = rect.bottom - area.top > area.height / 2
    setPending({
      selection: found,
      forText: text,
      left: Math.max(0, Math.min(rect.right - area.left, area.width - POPUP_WIDTH)),
      placement: lower
        ? { bottom: Math.max(0, area.bottom - rect.top + 6) }
        : { top: rect.bottom - area.top + 6 },
    })
    setLabel('')
  }

  function create() {
    if (!active || !onCreateEntity || label === '') return
    onCreateEntity(active.selection, label)
    clear()
    window.getSelection()?.removeAllRanges()
  }
  // The word being edited. It is only valid for the words it was chosen from: a
  // new text has new words, and then nothing is being edited.
  const [editing, setEditing] = useState<{
    word: Word
    among: Word[] | null
  } | null>(null)
  const editedWord = editing && editing.among === words ? editing.word : null

  const shown = (entities ?? []).filter(
    (entity) => decisions[entityKey(entity)]?.kind !== 'rejected',
  )
  const segments = buildSegments(text, words ?? [], shown)

  // The editor takes the place of the first segment of the word; the others
  // (a word that an entity cuts in two) are left out meanwhile.
  // Words that are only a space apart can be joined (not across a line break
  // or punctuation).
  const codePoints = Array.from(text)
  const apart = (before: Word, after: Word) =>
    codePoints.slice(before.end, after.start).join('') === ' '
  const sorted = [...(words ?? [])].sort((a, b) => a.start - b.start)
  const editorAt = segments.findIndex((segment) => segment.word === editedWord)
  function render(segment: Segment, index: number): ReactNode {
    const { word } = segment
    const key = `s${index}`
    if (!word) return segment.text
    if (word === editedWord) {
      if (index !== editorAt) return null
      const at = sorted.indexOf(word)
      const previous = sorted[at - 1]
      const next = sorted[at + 1]
      const joinWith = (pair: Word[]) => () => {
        setEditing(null)
        onJoinWords?.(pair)
      }
      return (
        <WordEditor
          key={key}
          word={word}
          busy={busy}
          onSubmit={(after) => onCorrectWord(word, after)}
          onJoinPrevious={
            onJoinWords && previous && apart(previous, word)
              ? joinWith([previous, word])
              : undefined
          }
          onJoinNext={
            onJoinWords && next && apart(word, next)
              ? joinWith([word, next])
              : undefined
          }
          onLegal={() => onLegalSpelling(word)}
          onCancel={() => setEditing(null)}
        />
      )
    }
    const edit = () => setEditing({ word, among: words })
    return (
      <span
        key={key}
        className={word.correct ? 'word' : 'word misspelled'}
        // misspelled words can be reached with the keyboard too
        tabIndex={word.correct ? undefined : 0}
        onClick={edit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') edit()
        }}
      >
        {segment.text}
      </span>
    )
  }

  const items: ReactNode[] = []
  for (let i = 0; i < segments.length; ) {
    const { entity } = segments[i]
    if (!entity) {
      items.push(render(segments[i], i))
      i++
      continue
    }
    const group: Segment[] = []
    const first = i
    while (i < segments.length && segments[i].entity === entity) {
      group.push(segments[i])
      i++
    }
    items.push(
      <EntityMark
        key={`e${first}`}
        entity={entity}
        decision={decisions[entityKey(entity)]}
        busy={busy}
        editing={editedWord !== null && group.some((s) => s.word === editedWord)}
        onConfirm={onConfirm}
        onReject={onReject}
      >
        {group.map((segment, index) => render(segment, first + index))}
      </EntityMark>,
    )
  }

  const pre = (
    <pre
      ref={box}
      className="page-box"
      role="region"
      aria-label="Page text"
      tabIndex={0}
      onMouseUp={onCreateEntity ? read : undefined}
      onKeyUp={onCreateEntity ? read : undefined}
      onDoubleClick={onAddToken ? doubleClicked : undefined}
    >
      {items}
    </pre>
  )
  if (!onCreateEntity && !onAddToken) return pre

  return (
    <div className="structure-wrap" ref={wrap}>
      <p className="structure-hint">
        {onCreateEntity
          ? 'Select some text to mark it as a named entity the recognition missed.'
          : 'Double-click a place in the text to add text that the OCR missed.'}
      </p>
      {pre}
      {addingAt && (
        <div
          role="dialog"
          aria-label="Add a text token"
          className={`structure-popup ${'bottom' in addingAt.placement ? 'above' : 'below'}`}
          style={{ left: addingAt.left, ...addingAt.placement }}
        >
          <input
            aria-label="Text to add"
            placeholder="Text"
            size={20}
            maxLength={200}
            autoFocus
            spellCheck={false}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !busy) add()
            }}
          />
          <button
            type="button"
            onClick={add}
            disabled={token.trim() === '' || busy}
          >
            Add
          </button>
        </div>
      )}
      {active && (
        <div
          role="dialog"
          aria-label="Mark the selected text as a named entity"
          className={`structure-popup ${'bottom' in active.placement ? 'above' : 'below'}`}
          style={{ left: active.left, ...active.placement }}
        >
          <select
            aria-label="Named entity"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          >
            <option value="">Choose a label…</option>
            {ENTITY_LABELS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
            {TEXT_LABELS.map(({ label: option, description }) => (
              <option key={option} value={option}>
                {option} ({description})
              </option>
            ))}
          </select>
          <button type="button" onClick={create} disabled={label === '' || busy}>
            Confirm
          </button>
        </div>
      )}
    </div>
  )
}
