import { useState, type FormEvent } from 'react'
import { getPage, pageNumberFromUrl, type Page } from './api'
import PageViewer from './PageViewer'

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

type Status =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'done'; page: Page }
  | { kind: 'error'; message: string }

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed'
}

export default function App() {
  const [uuid, setUuid] = useState('')
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [navigating, setNavigating] = useState(false)
  const [navError, setNavError] = useState<string | null>(null)

  const trimmed = uuid.trim()
  const valid = UUID_PATTERN.test(trimmed)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    setStatus({ kind: 'loading' })
    try {
      const page = await getPage(
        trimmed,
        pageNumberFromUrl(window.location.search),
      )
      setStatus({ kind: 'done', page })
    } catch (error) {
      setStatus({ kind: 'error', message: errorMessage(error) })
    }
  }

  /** Show another page of the same edition and reflect it in the URL. */
  async function goToPage(editionId: string, number: number) {
    setNavigating(true)
    setNavError(null)
    try {
      const page = await getPage(editionId, number)
      const url = new URL(window.location.href)
      url.searchParams.set('page', String(number))
      window.history.replaceState(null, '', url)
      setStatus({ kind: 'done', page })
    } catch (error) {
      setNavError(errorMessage(error))
    } finally {
      setNavigating(false)
    }
  }

  if (status.kind === 'done') {
    const { page } = status
    return (
      <PageViewer
        page={page}
        busy={navigating}
        error={navError}
        onPrevious={() => goToPage(page.edition_id, page.number - 1)}
        onNext={() => goToPage(page.edition_id, page.number + 1)}
      />
    )
  }

  return (
    <main>
      <form onSubmit={handleSubmit}>
        <label htmlFor="uuid">Edition UUID</label>
        <input
          id="uuid"
          value={uuid}
          onChange={(event) => setUuid(event.target.value)}
          placeholder="00000000-0000-0000-0000-000000000000"
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" disabled={!valid || status.kind === 'loading'}>
          Open
        </button>
      </form>
      {status.kind === 'error' && <p role="alert">{status.message}</p>}
    </main>
  )
}
