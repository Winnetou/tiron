export const DEFAULT_PAGE_NUMBER = 0

/** Response of GET /editions/{edition_id}/pages/{number} (PageView). */
export interface Page {
  id: string
  edition_id: string
  number: number
  scan_uri: string | null
  ready: boolean
  /** The corrected text if there is one, else the raw OCR text. */
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

/** Calls GET /editions/{editionId}/pages/{number}. */
export async function getPage(
  editionId: string,
  number: number = DEFAULT_PAGE_NUMBER,
): Promise<Page> {
  const response = await fetch(`/editions/${editionId}/pages/${number}`)
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
