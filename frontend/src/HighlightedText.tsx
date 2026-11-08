import { useState } from 'react'
import {
  entityKey,
  highlightParts,
  labelOptions,
  type Decision,
  type Entity,
} from './entities'

const NOT_NER = 'Not NER'

interface EntityMarkProps {
  entity: Entity
  text: string
  decision: Decision | undefined
  busy: boolean
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
  text,
  decision,
  busy,
  onConfirm,
  onReject,
}: EntityMarkProps) {
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState(entity.label)
  const confirmed = decision?.kind === 'confirmed'
  const group = `entity-${entity.start}-${entity.end}`

  function confirm() {
    setOpen(false)
    if (choice === NOT_NER) onReject(entity)
    else onConfirm(entity, choice)
  }

  return (
    <span
      className="entity-wrap"
      onMouseEnter={() => setOpen(!confirmed)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(!confirmed)}
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
      >
        {text}
      </mark>
      {open && (
        <div
          role="dialog"
          aria-label={`Named entity: ${entity.text}`}
          className="entity-dialog"
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

interface Props {
  text: string
  /** Null until the entities have been recognized. */
  entities: Entity[] | null
  /** What was decided about the entities so far, by entityKey. */
  decisions: Record<string, Decision>
  /** True while a request is running: nothing can be confirmed meanwhile. */
  busy: boolean
  onConfirm: (entity: Entity, label: string) => void
  onReject: (entity: Entity) => void
}

/**
 * The text of a page, with the recognized entities marked in red (the ones
 * that were rejected as "Not NER" are not marked any more).
 */
export default function HighlightedText({
  text,
  entities,
  decisions,
  busy,
  onConfirm,
  onReject,
}: Props) {
  const shown = (entities ?? []).filter(
    (entity) => decisions[entityKey(entity)]?.kind !== 'rejected',
  )
  return (
    <pre className="page-box" role="region" aria-label="Page text" tabIndex={0}>
      {highlightParts(text, shown).map((part, index) =>
        part.entity ? (
          <EntityMark
            key={index}
            entity={part.entity}
            text={part.text}
            decision={decisions[entityKey(part.entity)]}
            busy={busy}
            onConfirm={onConfirm}
            onReject={onReject}
          />
        ) : (
          part.text
        ),
      )}
    </pre>
  )
}
