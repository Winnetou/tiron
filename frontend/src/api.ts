import type { Entity } from './entities'

export const DEFAULT_PAGE_NUMBER = 0

/** Response of GET /editions/{edition_id}/pages/{number} (PageView). */
export interface Page {
  id: string
  edition_id: string
  number: number
  scan_uri: string | null
  ready: boolean
  /**
   * The corrected content if there is one, else the raw OCR text. For a page
   * fetched from Polona this is TEI XML: don't show it, turn it into text with
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

/** Page number from the URL's ?page= parameter; the default if absent/invalid. */
export function pageNumberFromUrl(search: string): number {
  const value = new URLSearchParams(search).get('page')
  return value !== null && /^\d+$/.test(value)
    ? Number(value)
    : DEFAULT_PAGE_NUMBER
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

const pageUrl = (editionId: string, number: number) =>
  `/editions/${editionId}/pages/${number}`

/** Calls GET /editions/{editionId}/pages/{number}. */
export function getPage(
  editionId: string,
  number: number = DEFAULT_PAGE_NUMBER,
): Promise<Page> {
  return request<Page>(pageUrl(editionId, number))
}

/** The only things of a page that can be edited. */
export interface PageChanges {
  corrected_content?: string | null
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
