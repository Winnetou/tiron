import { highlightParts, type Entity } from './entities'

/** The text of a page, with the named entities marked in red. */
export default function HighlightedText({
  text,
  entities,
}: {
  text: string
  /** Null until the entities have been recognized. */
  entities: Entity[] | null
}) {
  return (
    <pre className="page-box" role="region" aria-label="Page text" tabIndex={0}>
      {highlightParts(text, entities ?? []).map((part, index) =>
        part.entity ? (
          <mark key={index} className="entity" title={part.entity.label}>
            {part.text}
          </mark>
        ) : (
          part.text
        ),
      )}
    </pre>
  )
}
