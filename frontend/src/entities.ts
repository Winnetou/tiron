/** A named entity found by the backend: `text.slice(start, end) === entity.text`. */
export interface Entity {
  text: string
  label: string
  /** Character offsets, counted in code points (as Python does). */
  start: number
  end: number
}

export interface TextPart {
  text: string
  /** Set for the parts that are a named entity. */
  entity?: Entity
}

/**
 * For each code point of `text`, where it starts in the JavaScript string
 * (which counts UTF-16 code units, so it differs after characters like emoji).
 * One entry more than there are code points: the end of the text.
 */
function codePointStarts(text: string): number[] {
  const starts: number[] = []
  let unit = 0
  for (const codePoint of text) {
    starts.push(unit)
    unit += codePoint.length
  }
  starts.push(unit)
  return starts
}

/**
 * Cut `text` into plain parts and entity parts, using the offsets the backend
 * gave. Nothing is searched for: an entity is exactly the stretch the backend
 * pointed at, so the same word elsewhere in the text is left alone.
 *
 * An entity that does not fit is skipped rather than guessed at: one that
 * overlaps the previous entity, lies outside the text, or whose text is not
 * what is at its offsets (the text has changed since the backend saw it).
 */
export function highlightParts(text: string, entities: Entity[]): TextPart[] {
  const starts = codePointStarts(text)
  const parts: TextPart[] = []
  let cursor = 0
  for (const entity of [...entities].sort((a, b) => a.start - b.start)) {
    const from = starts[entity.start]
    const to = starts[entity.end]
    if (from === undefined || to === undefined) continue
    if (from < cursor || to <= from) continue
    if (text.slice(from, to) !== entity.text) continue
    if (from > cursor) parts.push({ text: text.slice(cursor, from) })
    parts.push({ text: text.slice(from, to), entity })
    cursor = to
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) })
  return parts
}
