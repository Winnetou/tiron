import type { Entity } from './entities'
import type { Word } from './segments'
import type { SectionKind, TextSelection } from './sections'

export const DEFAULT_PAGE_NUMBER = 0

/** Response of GET /editions/{edition_id}/pages/{number} (PageView). */
export interface Page {
  id: string
  edition_id: string
  number: number
  scan_uri: string | null
  ready: boolean
  /**
   * The page's TEI XML (the backend's xml_content_corrected). The raw OCR text
   * never reaches the frontend. Don't show the XML: turn it into text with
   * teiToText.
   */
  content: string
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * The path of a page: on the backend, and on the frontend too, so the address
 * of a page in the browser names its edition and its number, as the endpoint does.
 */
export const pageUrl = (editionId: string, number: number) =>
  `/editions/${editionId}/pages/${number}`

/**
 * The edition and the page number in a path like /editions/{uuid}/pages/{number}
 * (the one `pageUrl` makes); null for any other path.
 */
export function pageFromPath(
  pathname: string,
): { editionId: string; number: number } | null {
  const match = /^\/editions\/([^/]+)\/pages\/(\d+)\/?$/.exec(pathname)
  return match ? { editionId: match[1], number: Number(match[2]) } : null
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await (init ? fetch(url, init) : fetch(url))
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // body was not JSON; keep the status text
    }
    throw new ApiError(response.status, detail)
  }
  return response.json()
}

/** A page of the collection: GET /editions, 100 edition ids on a page. */
export interface EditionsPage {
  items: string[]
  total: number | null
  /** The number of this page, from 1. */
  page: number | null
  size: number | null
  /** How many pages the collection has. */
  pages: number | null
}

/** Calls GET /editions?page=: the ids of the editions of the collection. */
export function getEditions(page: number = 1): Promise<EditionsPage> {
  return request<EditionsPage>(`/editions?page=${page}`)
}

/** Calls GET /editions/{editionId}/pages/{number}. */
export function getPage(
  editionId: string,
  number: number = DEFAULT_PAGE_NUMBER,
): Promise<Page> {
  return request<Page>(pageUrl(editionId, number))
}

/** The only things of a page that can be edited. */
export interface PageChanges {
  xml_content_corrected?: string
  ready?: boolean
}

/** Calls PATCH /editions/{editionId}/pages/{number}; returns the updated page. */
export function updatePage(
  editionId: string,
  number: number,
  changes: PageChanges,
): Promise<Page> {
  return request<Page>(pageUrl(editionId, number), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })
}

/**
 * Calls POST /tei/text: the backend validates the TEI XML and returns just its
 * text. The backend is the only judge of whether the XML is correct, so a bad
 * document rejects with an ApiError whose message is the backend's reason.
 */
export async function teiToText(xml: string): Promise<string> {
  const { text } = await request<{ text: string }>('/tei/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ xml }),
  })
  return text
}

/** A section marked in the XML: its place in the text, and its tags. */
export interface SectionSpan {
  /** Offsets in the text, in code points (as the backend counts). */
  start: number
  end: number
  /** The tags as written in the XML, e.g. `<note n="1">` and `</note>`. */
  open: string
  close: string
}

/**
 * Calls POST /tei/sections: the backend lists the sections that are marked in
 * the XML (headers, footers, page numbers, the table of contents, notes and
 * note groups) with where they are in the text and what their tags are, so that
 * the structure view can show them. The frontend does not read the XML itself.
 */
export async function teiSections(xml: string): Promise<SectionSpan[]> {
  const { sections } = await request<{ sections: SectionSpan[] }>(
    '/tei/sections',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ xml }),
    },
  )
  return sections
}

/**
 * Calls POST /tei/entities: the named entities that are marked in the XML (the
 * elements `tagEntity` makes, whoever put them there), with where they are in
 * the text, like the entities that the recognition finds. The frontend does not
 * read the XML itself.
 */
export async function teiEntities(xml: string): Promise<Entity[]> {
  const { entities } = await request<{ entities: Entity[] }>('/tei/entities', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ xml }),
  })
  return entities
}

/**
 * Calls POST /ner: the backend finds the named entities (people, places,
 * organisations, dates, ...) in a plain text. Each has the character offsets
 * `start` and `end` in the text that was sent.
 */
export async function recognizeEntities(text: string): Promise<Entity[]> {
  const { entities } = await request<{ entities: Entity[] }>('/ner', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  return entities
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/entities: the backend puts
 * the entity inside a `label` element (<persName>, <placeName>, <date>, ...) in
 * the XML of the page, saves it, and returns the updated page. The frontend
 * only says which stretch of the text it is (the offsets it got from /ner);
 * finding the place in the XML is the backend's job.
 */
export function tagEntity(
  editionId: string,
  number: number,
  entity: Entity,
  label: string,
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/entities`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: entity.start,
      end: entity.end,
      text: entity.text,
      label,
    }),
  })
}

/**
 * Calls POST /spellcheck: the backend lists every word of a plain text, with
 * its character offsets and whether it is spelled correctly. The backend alone
 * decides what a word is. With the edition the text is from, the spellings that
 * were made legal in that edition count as correct.
 */
export async function spellcheck(
  text: string,
  editionId?: string,
): Promise<Word[]> {
  const { words } = await request<{ words: Word[] }>('/spellcheck', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(
      editionId === undefined ? { text } : { text, edition_id: editionId },
    ),
  })
  return words
}

/**
 * Calls POST /editions/{editionId}/legal-spellings: the backend records that a
 * spelling is legal in the edition, although the dictionary does not know it.
 * We say "legal", not "correct": the dictionary is a modern one, and what was
 * correct in the 19th century often is not anymore. We have no better word.
 * Sending one that is already recorded is fine.
 */
export async function addLegalSpelling(
  editionId: string,
  spelling: string,
): Promise<void> {
  await request<unknown>(`/editions/${editionId}/legal-spellings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spelling }),
  })
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/corrections: the backend
 * replaces the word with `after` in the XML of the page, saves it and records
 * the correction (before -> after), and returns the updated page. Like for
 * entities, the frontend only gives the word's offsets and the new text.
 */
/**
 * Calls POST /editions/{editionId}/pages/{number}/tokens: the backend puts a text
 * token that the OCR missed at `position` of the page's text (between two
 * characters), in the XML, and returns the updated page. No correction is
 * recorded for it.
 */
export function addToken(
  editionId: string,
  number: number,
  position: number,
  text: string,
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/tokens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ position, text }),
  })
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/joins: the backend removes the
 * spaces between `words` (the OCR split a word in two: "abso lutely") in the XML
 * of the page, and returns the updated page. No correction is recorded for it.
 */
export function joinTokens(
  editionId: string,
  number: number,
  words: Word[],
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/joins`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: words[0].start,
      end: words[words.length - 1].end,
      text: words.map((word) => word.text).join(' '),
    }),
  })
}

/** A token of a page's text that has the mistake of a correction (TokenMatch). */
export interface TokenMatch {
  text: string
  /** Offsets in the text of the page, counted in code points. */
  start: number
  end: number
  /** The token with the mistake corrected: what a correction of it would be. */
  suggestion: string
}

/** The tokens of one page of the edition that have the mistake (PageMatches). */
export interface PageMatches {
  page_number: number
  tokens: TokenMatch[]
}

/**
 * Calls GET /editions/{editionId}/similar-misspellings: the backend finds the
 * places of the edition that have the same OCR mistake as a correction of
 * `incorrect` to `corrected`, page by page, with the token's offsets and what it
 * would be corrected to. Nothing is changed.
 */
export function getSimilarMisspellings(
  editionId: string,
  incorrect: string,
  corrected: string,
): Promise<PageMatches[]> {
  const query = new URLSearchParams({
    incorrect_spelling: incorrect,
    corrected_spelling: corrected,
  })
  return request<PageMatches[]>(
    `/editions/${editionId}/similar-misspellings?${query}`,
  )
}

/** A printed page number found in the text of a page (PageNumberMatch). */
export interface PageNumberMatch {
  page_number: number
  /** What to mark: offsets in the text of the page, counted in code points. */
  start: number
  end: number
  text: string
  /** The digits of the printed number, as a number. */
  number: number
  /** The start of the line next to it, to see where it is. */
  context: string
  /** Does it follow the numbering of the example (page less number is the same)? */
  fits_sequence: boolean
}

/** The page numbers of an edition that are like a marked one (SimilarPageNumbers). */
export interface SimilarPageNumbers {
  example: PageNumberMatch
  /** The line of the page the number is on: the first or the last. */
  where: string
  /** The page of the example less its printed number: the same for a numbering. */
  offset: number
  candidates: PageNumberMatch[]
}

/**
 * Calls GET /editions/{editionId}/pages/{number}/similar-page-numbers: the backend
 * takes the page number that is marked on that page as an example, and lists the
 * pages of the edition that have a number like it on the same line and none
 * marked yet, with what to mark in each. Nothing is changed.
 */
export function getSimilarPageNumbers(
  editionId: string,
  number: number,
): Promise<SimilarPageNumbers> {
  return request<SimilarPageNumbers>(
    `${pageUrl(editionId, number)}/similar-page-numbers`,
  )
}

/** A line of a page that is OCR rubbish, not text (RubbishLine). */
export interface RubbishLine {
  /** The whole line: offsets in the text of the page, counted in code points. */
  start: number
  end: number
  text: string
  /** False if the line has a correct word in it: it may be text with punctuation. */
  sure: boolean
}

/** The rubbish lines of one page of an edition (PageRubbish). */
export interface PageRubbish {
  page_number: number
  lines: RubbishLine[]
}

/**
 * Calls GET /editions/{editionId}/rubbish-lines: the backend finds the lines of
 * the whole edition that are OCR rubbish (an ornament read as letters and signs),
 * page by page, with the offsets of each. Nothing is changed.
 */
export function getRubbishLines(editionId: string): Promise<PageRubbish[]> {
  return request<PageRubbish[]>(`/editions/${editionId}/rubbish-lines`)
}

/** The page after a line was removed, and how many characters of its text went. */
export interface LineRemoved {
  page: Page
  removed: number
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/line-removals: the backend
 * removes the whole `line` (and its line break) from the XML of the page. The
 * answer is the updated page and how many characters of the text went, to move
 * the offsets of what follows by. No correction is recorded for it.
 */
export function removeLine(
  editionId: string,
  number: number,
  line: RubbishLine,
): Promise<LineRemoved> {
  return request<LineRemoved>(`${pageUrl(editionId, number)}/line-removals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start: line.start, end: line.end, text: line.text }),
  })
}

/** A line end of a page where the OCR dropped a hyphen (MissingHyphen). */
export interface MissingHyphen {
  /** What to replace: offsets in the text of the page, counted in code points. */
  start: number
  end: number
  /** The word at the end of the line, with the sign taken for the hyphen, if any. */
  text: string
  /** The word and a hyphen: what `text` is to become. */
  suggestion: string
  /** The word that starts the next line. */
  next_word: string
  /** The word that the two parts make together (the one that was found correct). */
  joined: string
  /** Both parts are correct words on their own: it is not sure the word is split. */
  ambiguous: boolean
}

/**
 * Calls GET /editions/{editionId}/pages/{number}/missing-hyphens: the backend finds
 * the line ends of that page where a hyphen of a split word is missing. Nothing is
 * changed.
 */
export function getMissingHyphens(
  editionId: string,
  number: number,
): Promise<MissingHyphen[]> {
  return request<MissingHyphen[]>(`${pageUrl(editionId, number)}/missing-hyphens`)
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/hyphens: the backend puts the
 * hyphens back at the places, all at once (all or none), and returns the updated
 * page. The offsets are those of the page as the client saw it.
 */
export function putHyphensBack(
  editionId: string,
  number: number,
  hyphens: MissingHyphen[],
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/hyphens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      fixes: hyphens.map((hyphen) => ({
        start: hyphen.start,
        end: hyphen.end,
        before: hyphen.text,
        after: hyphen.suggestion,
      })),
    }),
  })
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/line-splits: the backend turns
 * the space between `words` (two neighbouring words of one line) into a line
 * break in the XML of the page, and returns the updated page. No correction is
 * recorded for it.
 */
export function splitLine(
  editionId: string,
  number: number,
  words: Word[],
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/line-splits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: words[0].start,
      end: words[words.length - 1].end,
      text: words.map((word) => word.text).join(' '),
    }),
  })
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/line-joins: the backend removes
 * the line break between `words` (the last word of a line and the first one of
 * the next line) in the XML of the page, or the empty line between two
 * paragraphs (which becomes a line break), and returns the updated page. No
 * correction is recorded for it. `pageText` is the text the offsets are in.
 */
export function joinLines(
  editionId: string,
  number: number,
  words: Word[],
  pageText: string,
): Promise<Page> {
  const start = words[0].start
  const end = words[words.length - 1].end
  return request<Page>(`${pageUrl(editionId, number)}/line-joins`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start,
      end,
      // the two words with what is between them: a line break, or an empty line
      text: Array.from(pageText).slice(start, end).join(''),
    }),
  })
}

export function correctWord(
  editionId: string,
  number: number,
  word: Word,
  after: string,
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/corrections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: word.start,
      end: word.end,
      before: word.text,
      after,
    }),
  })
}

/**
 * Calls POST /editions/{editionId}/pages/{number}/sections: the backend marks
 * the selected text as a section of the page (a header, a footer, the page
 * number or the table of contents) in the XML of the page, saves it, and returns
 * the updated page. The frontend only says which stretch of the text it is and
 * which section; the backend finds the place in the XML and chooses the TEI
 * element.
 */
export function markSection(
  editionId: string,
  number: number,
  selection: TextSelection,
  section: SectionKind,
  n?: string,
): Promise<Page> {
  return request<Page>(`${pageUrl(editionId, number)}/sections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      start: selection.start,
      end: selection.end,
      text: selection.text,
      section,
      // the number of a note, if given: <note n="1">
      ...(n ? { n } : {}),
    }),
  })
}
