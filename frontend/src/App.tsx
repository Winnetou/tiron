import { useState, type FormEvent } from 'react'
import { getPage, pageNumberFromUrl, updatePage, type Page, type PageChanges } from './api'
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
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

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

  /** Run a request for the open page, showing the page it returns. */
  async function run(request: Promise<Page>, onSuccess?: (page: Page) => void) {
    setBusy(true)
    setActionError(null)
    try {
      const page = await request
      onSuccess?.(page)
      setStatus({ kind: 'done', page })
    } catch (error) {
      setActionError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  /** Show another page of the same edition and reflect it in the URL. */
  function goToPage(editionId: string, number: number) {
    return run(getPage(editionId, number), () => {
      const url = new URL(window.location.href)
      url.searchParams.set('page', String(number))
      window.history.replaceState(null, '', url)
    })
  }

  /** Change the corrected content and/or the ready flag of the open page. */
  function savePage(page: Page, changes: PageChanges) {
    return run(updatePage(page.edition_id, page.number, changes))
  }

  if (status.kind === 'done') {
    const { page } = status
    return (
      <PageViewer
        page={page}
        busy={busy}
        error={actionError}
        onPrevious={() => goToPage(page.edition_id, page.number - 1)}
        onNext={() => goToPage(page.edition_id, page.number + 1)}
        onSave={(content) => savePage(page, { corrected_content: content })}
        onMarkReady={() => savePage(page, { ready: true })}
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
