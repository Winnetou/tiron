import type { ReactNode } from 'react'

interface Tool {
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

const TOOLS: Tool[] = [
  {
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

/** Vertical tool bar on the right. The buttons are stubs for now. */
export default function Sidebar() {
  return (
    <aside className="sidebar" aria-label="Tools">
      {TOOLS.map(({ label, icon }) => (
        <button
          key={label}
          type="button"
          className="sidebar-button"
          aria-label={label}
          data-tooltip={label}
        >
          {icon}
        </button>
      ))}
    </aside>
  )
}
