import type { ReactNode } from 'react'

/** The views that the sidebar switches between. */
export type SidebarView = 'correction' | 'structure' | 'ner'

interface Tool {
  view: SidebarView
  label: string
  icon: ReactNode
}

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

// In this order, from the top. The first is the view the page opens in.
const TOOLS: Tool[] = [
  {
    view: 'correction',
    label: 'Correction',
    // pencil
    icon: (
      <Icon>
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </Icon>
    ),
  },
  {
    view: 'structure',
    label: 'Structure',
    // outline: headings and indented lines
    icon: (
      <Icon>
        <path d="M4 6h16M8 12h12M8 18h12" />
        <path d="M4 12h.01M4 18h.01" />
      </Icon>
    ),
  },
  {
    view: 'ner',
    label: 'Named entity recognition',
    // tag
    icon: (
      <Icon>
        <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z" />
        <path d="M7.5 7.5h.01" />
      </Icon>
    ),
  },
]

interface Props {
  /** The view that is shown, which is the button that is on; none for the XML. */
  active: SidebarView | null
  onSelect: (view: SidebarView) => void
  /** False when there is no text to recognize entities in. */
  canRecognize: boolean
  recognizing: boolean
}

/**
 * Vertical tool bar on the right: one button for each view of the page
 * (correction, structure, named entity recognition). The button of the view that
 * is shown is on (pressed and highlighted), the others are off.
 */
export default function Sidebar({
  active,
  onSelect,
  canRecognize,
  recognizing,
}: Props) {
  return (
    <aside className="sidebar" aria-label="Tools">
      {TOOLS.map(({ view, label, icon }) => {
        const isNer = view === 'ner'
        return (
          <button
            key={view}
            type="button"
            className="sidebar-button"
            aria-label={label}
            data-tooltip={label}
            onClick={() => onSelect(view)}
            disabled={isNer && (!canRecognize || recognizing)}
            aria-busy={isNer && recognizing ? true : undefined}
            aria-pressed={active === view}
          >
            {icon}
          </button>
        )
      })}
    </aside>
  )
}
