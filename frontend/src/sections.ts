/** The sections of a page that can be marked, as the backend names them. */
export type SectionKind = 'header' | 'footer' | 'page_number' | 'toc'

export interface SectionOption {
  kind: SectionKind
  /** What the dropdown shows. */
  label: string
}

/**
 * What the dropdown offers, in order. The backend turns each into its TEI
 * element: a running header, a footer and the printed page number into
 * `<fw type="...">`, and the table of contents into `<div type="toc">`.
 */
export const SECTIONS: SectionOption[] = [
  { kind: 'header', label: 'Page header' },
  { kind: 'footer', label: 'Page footer' },
  { kind: 'page_number', label: 'Page number' },
  { kind: 'toc', label: 'Table of contents' },
]

export const sectionLabel = (kind: SectionKind): string =>
  SECTIONS.find((section) => section.kind === kind)?.label ?? kind

/** A stretch of the page's text, by code point offsets (as the backend counts). */
export interface TextSelection {
  start: number
  end: number
  text: string
}
