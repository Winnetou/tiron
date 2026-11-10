import type { TextSelection } from './sections'

/**
 * Where a selection made in the browser is in the text of a page: the offsets
 * in code points (as the backend counts), without the spaces and line breaks at
 * its ends, and the selected text.
 *
 * `container` is the element that shows `text` and nothing else, so that the
 * length of the text before a point is its offset in `text`. Returns null if the
 * selection is empty, is not (entirely) in the container, or if the container
 * does not show `text` (it has changed since).
 */
export function selectionInText(
  container: HTMLElement,
  range: Range,
  text: string,
): TextSelection | null {
  if (
    !container.contains(range.startContainer) ||
    !container.contains(range.endContainer) ||
    container.textContent !== text
  ) {
    return null
  }
  const offsetOf = (node: Node, offset: number): number => {
    const before = document.createRange()
    before.selectNodeContents(container)
    before.setEnd(node, offset)
    return before.toString().length
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
