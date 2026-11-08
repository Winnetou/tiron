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

/**
 * Every label the NER model can give an entity, which are also the TEI elements
 * the backend can mark it with (its `EntityLabel`: it rejects any other).
 */
export const ENTITY_LABELS = [
  'persName',
  'placeName',
  'orgName',
  'geogName',
  'date',
  'time',
] as const

/**
 * The labels to choose from for an entity: all of them, and the one it was
 * recognized with too, should the model ever give one that is not in the list.
 */
export function labelOptions(entity: Entity): string[] {
  const all: string[] = [...ENTITY_LABELS]
  return all.includes(entity.label) ? all : [entity.label, ...all]
}

/**
 * What the user decided about a recognized entity: that it is not one, or
 * that it is, with the label it was marked with in the XML (which is not
 * always the one the model gave it).
 */
export type Decision =
  | { kind: 'confirmed'; label: string }
  | { kind: 'rejected' }

/** Identifies an entity within a text, to remember what was decided about it. */
export const entityKey = (entity: Entity): string =>
  `${entity.start}:${entity.end}:${entity.text}`
