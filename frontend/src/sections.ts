/** The sections of a page that can be marked, as the backend names them. */
export type SectionKind =
  | 'header'
  | 'footer'
  | 'page_number'
  | 'toc'
  | 'note_group'
  | 'note'
  | 'quote'

export interface SectionOption {
  kind: SectionKind
  /** What the dropdown shows. */
  label: string
}

/**
 * What the dropdown offers, in order. The backend turns each into its TEI
 * element: a running header, a footer and the printed page number into
 * `<fw type="...">`, the table of contents into `<div type="toc">`, a note
 * into `<note>` (with its number as `n`, if given) and a group of notes that are
 * marked already into `<noteGrp>`.
 */
export const SECTIONS: SectionOption[] = [
  { kind: 'header', label: 'Page header' },
  { kind: 'footer', label: 'Page footer' },
  { kind: 'page_number', label: 'Page number' },
  { kind: 'toc', label: 'Table of contents' },
  { kind: 'note_group', label: 'Note group' },
  { kind: 'note', label: 'Note' },
  { kind: 'quote', label: 'Quote in another language' },
]

/**
 * The languages that a quotation can be in, by the code that goes into
 * `xml:lang`: the most used European ones, and Latin. The backend has the same
 * list and refuses any other.
 */
export const LANGUAGES: { code: string; label: string }[] = [
  { code: 'la', label: 'Latin' },
  { code: 'de', label: 'German' },
  { code: 'fr', label: 'French' },
  { code: 'en', label: 'English' },
  { code: 'it', label: 'Italian' },
  { code: 'es', label: 'Spanish' },
  { code: 'ru', label: 'Russian' },
]

export const sectionLabel = (kind: SectionKind): string =>
  SECTIONS.find((section) => section.kind === kind)?.label ?? kind

/** A stretch of the page's text, by code point offsets (as the backend counts). */
export interface TextSelection {
  start: number
  end: number
  text: string
}
