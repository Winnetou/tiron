import type { ReactNode } from 'react'

interface Tool {
  label: string
  icon: ReactNode
}

const NER_LABEL = 'Named entity recognition'
const STRUCTURE_LABEL = 'Structure'
const CORRECTION_LABEL = 'Correction'

// Simple 24x24 stroke icons (no icon dependency).
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

const TOOLS: Tool[] = [
  {
    label: CORRECTION_LABEL,
    // pencil
    icon: (
      <Icon>
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </Icon>
    ),
  },
  {
    label: STRUCTURE_LABEL,
    // outline: headings and indented lines
    icon: (
      <Icon>
        <path d="M4 6h16M8 12h12M8 18h12" />
        <path d="M4 12h.01M4 18h.01" />
      </Icon>
    ),
  },
  {
    label: NER_LABEL,
    // tag
    icon: (
      <Icon>
        <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z" />
        <path d="M7.5 7.5h.01" />
      </Icon>
    ),
  },
]

export type SidebarView = 'correction' | 'structure' | 'ner' | 'text' | 'xml'

interface Props {
  /** The view that is shown: its button is the active one. */
  view: SidebarView
  /** Switch to another view (named entity recognition has its own handler). */
  onShow: (view: Exclude<SidebarView, 'ner'>) => void
  /** Send the text of the page to be recognized for named entities, and show them. */
  onRecognizeEntities: () => void
  /** False when there is no text to recognize entities in. */
  canRecognize: boolean
  recognizing: boolean
}

/**
 * Vertical tool bar on the right: the views of the page, the active one
 * pressed. Correction (the spelling), Structure (to mark the sections of the
 * page) and Named entity recognition. Below them: Text (the text of the page,
 * as the backend extracts it from the XML) and XML (the XML itself).
 */
export default function Sidebar({
  view,
  onShow,
  onRecognizeEntities,
  canRecognize,
  recognizing,
}: Props) {
  return (
    <aside className="sidebar" aria-label="Tools">
      {TOOLS.map(({ label, icon }) => {
        const isNer = label === NER_LABEL
        const target = label === STRUCTURE_LABEL ? 'structure' : 'correction'
        const active =
          view === (isNer ? 'ner' : target)
        return (
          <button
            key={label}
            type="button"
            className="sidebar-button"
            aria-label={label}
            data-tooltip={label}
            onClick={isNer ? onRecognizeEntities : () => onShow(target)}
            disabled={isNer && (!canRecognize || recognizing)}
            aria-busy={isNer && recognizing ? true : undefined}
            aria-pressed={active}
          >
            {icon}
          </button>
        )
      })}
      <div className="view-switch" role="group" aria-label="Show">
        {(['text', 'xml'] as const).map((choice) => (
          <button
            key={choice}
            type="button"
            aria-pressed={view === choice}
            onClick={() => onShow(choice)}
          >
            {choice === 'text' ? 'Text' : 'XML'}
          </button>
        ))}
      </div>
    </aside>
  )
}
