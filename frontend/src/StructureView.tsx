import { useEffect, useRef, useState } from 'react'
import { selectionInText } from './selection'
import { SECTIONS, type SectionKind, type TextSelection } from './sections'

/** About how wide the dialog is, to keep it inside the box. */
const POPUP_WIDTH = 270

interface Pending {
  selection: TextSelection
  /** The text the selection was made in: it is only valid for that text. */
  forText: string
  /** Where the dialog goes, in the box: next to the end of the selection. */
  left: number
  top: number
}

interface Props {
  text: string
  /** True while a request is running: nothing can be confirmed meanwhile. */
  busy: boolean
  /** The user chose a section for the selected text and confirmed. */
  onMark: (selection: TextSelection, section: SectionKind) => void
}

/**
 * The text of the page, to mark the sections of it. Select some of the text and
 * a dialog appears next to the selection with a dropdown of the sections (page
 * header, page footer, page number, table of contents) and a Confirm button.
 * The frontend only says which text it is and which section: the backend puts
 * the text inside the right element in the XML.
 */
export default function StructureView({ text, busy, onMark }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLPreElement>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [choice, setChoice] = useState<SectionKind | ''>('')
  const active = pending && pending.forText === text ? pending : null

  function clear() {
    setPending(null)
    setChoice('')
  }

  // Escape closes the dialog wherever the keyboard focus is.
  useEffect(() => {
    if (!active) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setPending(null)
        setChoice('')
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
    setPending({
      selection: found,
      forText: text,
      left: Math.max(0, Math.min(rect.right - area.left, area.width - POPUP_WIDTH)),
      top: rect.bottom - area.top + 6,
    })
    setChoice('')
  }

  function confirm() {
    if (!active || choice === '') return
    onMark(active.selection, choice)
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
        {text}
      </pre>
      {active && (
        <div
          role="dialog"
          aria-label="Mark the selected text"
          className="structure-popup"
          style={{ left: active.left, top: active.top }}
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
