import type { TextSelection } from './sections'

/**
 * Where a selection made in the browser is in the text of a page: the offsets
 * in code points (as the backend counts), without the spaces and line breaks at
 * its ends, and the selected text.
 *
 * `container` is the element that shows `text` and nothing else, so that the
 * length of the text before a point is its offset in `text`. The only other
 * thing it may hold is a dialog (role="dialog"), like the one that opens over a
 * recognized entity: its text is not the text of the page and is not counted.
 * Returns null if the selection is empty, is not (entirely) in the container or
 * is in a dialog, or if the container does not show `text` (it has changed
 * since).
 */
export function selectionInText(
  container: HTMLElement,
  range: Range,
  text: string,
): TextSelection | null {
  const dialogs = Array.from(container.querySelectorAll('[role="dialog"]'))
  const inDialog = (node: Node) => dialogs.some((dialog) => dialog.contains(node))
  if (
    !container.contains(range.startContainer) ||
    !container.contains(range.endContainer) ||
    inDialog(range.startContainer) ||
    inDialog(range.endContainer)
  ) {
    return null
  }
  const copy = container.cloneNode(true) as HTMLElement
  copy.querySelectorAll('[role="dialog"]').forEach((dialog) => dialog.remove())
  if (copy.textContent !== text) return null
  const offsetOf = (node: Node, offset: number): number => {
    const before = document.createRange()
    before.selectNodeContents(container)
    before.setEnd(node, offset)
    // (without the text of the dialogs that are entirely before the point)
    const skipped = dialogs
      .filter(
        (dialog) =>
          before.comparePoint(dialog, 0) === 0 &&
          before.comparePoint(dialog, dialog.childNodes.length) === 0,
      )
      .reduce((sum, dialog) => sum + (dialog.textContent ?? '').length, 0)
    return before.toString().length - skipped
  }
  let from = offsetOf(range.startContainer, range.startOffset)
  let to = offsetOf(range.endContainer, range.endOffset)
  while (from < to && /\s/.test(text[from])) from++
  while (to > from && /\s/.test(text[to - 1])) to--
  if (from >= to) return null

  const start = [...text.slice(0, from)].length
  const selected = text.slice(from, to)
  return { start, end: start + [...selected].length, text: selected }
}
