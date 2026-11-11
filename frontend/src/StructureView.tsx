import { useEffect, useRef, useState, type ReactNode } from 'react'
import { teiSections, type SectionSpan } from './api'
import { selectionInText } from './selection'
import { SECTIONS, type SectionKind, type TextSelection } from './sections'

/** About how wide the dialog is, to keep it inside the box. */
const POPUP_WIDTH = 350

interface Pending {
  selection: TextSelection
  /** The text the selection was made in: it is only valid for that text. */
  forText: string
  /** Where the dialog goes, in the box: next to the end of the selection. */
  left: number
  /**
   * Below the selection (`top` is its distance from the top of the box), or,
   * for a selection in the lower half of the box, above it (`bottom` is the
   * distance from the bottom of the box): else it would be out of sight.
   */
  placement: { top: number } | { bottom: number }
}

/**
 * The text with the marked sections around their parts, each with its tags
 * shown before and after it (see `.section` in the CSS: the tags are drawn by
 * the stylesheet, so they are not text: they cannot be edited or selected, and
 * the offsets of a selection are those of the text alone).
 */
function withSections(text: string, spans: SectionSpan[]): ReactNode[] {
  // The backend counts code points, strings here are in UTF-16 units.
  const units: number[] = []
  let unit = 0
  for (const char of text) {
    units.push(unit)
    unit += char.length
  }
  units.push(unit)
  const at = (point: number) => units[Math.min(point, units.length - 1)]

  // Outer sections first, as they start at the same place.
  const sorted = [...spans].sort((a, b) => a.start - b.start || b.end - a.end)
  let next = 0
  function parts(from: number, to: number): ReactNode[] {
    const out: ReactNode[] = []
    let position = from
    while (next < sorted.length && at(sorted[next].start) < to) {
      const span = sorted[next++]
      const start = at(span.start)
      const end = Math.min(at(span.end), to)
      if (start > position) out.push(text.slice(position, start))
      out.push(
        <span
          key={next}
          className="section"
          data-open={span.open}
          data-close={span.close}
        >
          {parts(start, end)}
        </span>,
      )
      position = end
    }
    if (position < to) out.push(text.slice(position, to))
    return out
  }
  return parts(0, text.length)
}

interface Props {
  text: string
  /** The XML of the page: the sections marked in it are shown in the text. */
  xml: string
  /** True while a request is running: nothing can be confirmed meanwhile. */
  busy: boolean
  /** The user chose a section for the selected text and confirmed. */
  onMark: (selection: TextSelection, section: SectionKind, n?: string) => void
}

/**
 * The text of the page, to mark the sections of it. Select some of the text and
 * a dialog appears next to the selection with a dropdown of the sections (page
 * header, page footer, page number, table of contents) and a Confirm button.
 * The frontend only says which text it is and which section: the backend puts
 * the text inside the right element in the XML.
 */
export default function StructureView({ text, xml, busy, onMark }: Props) {
  // The sections marked in the XML, by the backend, for the XML they are of. They
  // are shown once they are known: until then (or if they cannot be had) it is
  // just the text.
  const [found, setFound] = useState<{ xml: string; spans: SectionSpan[] } | null>(
    null,
  )
  useEffect(() => {
    let current = true
    teiSections(xml)
      .then((spans) => current && setFound({ xml, spans }))
      .catch(() => current && setFound(null))
    return () => {
      current = false
    }
  }, [xml])
  const spans = found && found.xml === xml ? found.spans : []

  const wrap = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLPreElement>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [choice, setChoice] = useState<SectionKind | ''>('')
  // The number of a note (<note n="1">), which is optional.
  const [number, setNumber] = useState('')
  const active = pending && pending.forText === text ? pending : null

  function clear() {
    setPending(null)
    setChoice('')
    setNumber('')
  }

  // Escape closes the dialog wherever the keyboard focus is.
  useEffect(() => {
    if (!active) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setPending(null)
        setChoice('')
        setNumber('')
        window.getSelection()?.removeAllRanges()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [active])

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
    setChoice('')
    setNumber('')
  }

  function confirm() {
    if (!active || choice === '') return
    onMark(active.selection, choice, choice === 'note' ? number.trim() : undefined)
    clear()
    window.getSelection()?.removeAllRanges()
  }

  return (
    <div className="structure-wrap" ref={wrap}>
      <p className="structure-hint">
        Select some text to mark it as a section of the page.
      </p>
      <pre
        ref={box}
        className="page-box structure-box"
        role="region"
        aria-label="Page text"
        tabIndex={0}
        onMouseUp={read}
        onKeyUp={read}
      >
        {spans.length === 0 ? text : withSections(text, spans)}
      </pre>
      {active && (
        <div
          role="dialog"
          aria-label="Mark the selected text"
          className={`structure-popup ${'bottom' in active.placement ? 'above' : 'below'}`}
          style={{ left: active.left, ...active.placement }}
        >
          <select
            aria-label="Section"
            value={choice}
            onChange={(event) => setChoice(event.target.value as SectionKind | '')}
          >
            <option value="">Choose a section…</option>
            {SECTIONS.map(({ kind, label }) => (
              <option key={kind} value={kind}>
                {label}
              </option>
            ))}
          </select>
          {choice === 'note' && (
            <input
              aria-label="Note number"
              placeholder="No."
              size={4}
              maxLength={20}
              value={number}
              onChange={(event) => setNumber(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy) confirm()
              }}
            />
          )}
          <button
            type="button"
            onClick={confirm}
            disabled={choice === '' || busy}
          >
            Confirm
          </button>
        </div>
      )}
    </div>
  )
}
