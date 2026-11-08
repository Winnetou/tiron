import { useState, type FormEvent } from 'react'
import {
  getPage,
  pageNumberFromUrl,
  recognizeEntities,
  teiToText,
  updatePage,
  type Page,
} from './api'
import type { Entity } from './entities'
import PageViewer from './PageViewer'

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** A page together with its text, which is what the viewer shows. */
interface PageWithText {
  page: Page
  text: string
  textError: string | null
}

type Status =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | ({ kind: 'done' } & PageWithText)
  | { kind: 'error'; message: string }

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed'
}

/**
 * The content of a page is TEI XML. As soon as we have it, it goes back to the
 * backend, which validates it and returns just the text; only that text is
 * shown. The backend is the only judge of whether the XML is correct: if it
 * says no, we show its reason and no text.
 */
async function withText(page: Page): Promise<PageWithText> {
  try {
    return { page, text: await teiToText(page.content), textError: null }
  } catch (error) {
    return { page, text: '', textError: errorMessage(error) }
  }
}

export default function App() {
  const [uuid, setUuid] = useState('')
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // The entities found in a text, with the text they belong to, so that they
  // are only shown while that same text is on the screen.
  const [recognized, setRecognized] = useState<{
    text: string
    entities: Entity[]
  } | null>(null)
  const [recognizing, setRecognizing] = useState(false)

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
      setStatus({ kind: 'done', ...(await withText(page)) })
    } catch (error) {
      setStatus({ kind: 'error', message: errorMessage(error) })
    }
  }

  /** Run a request for the open page, showing the page it returns. */
  async function run(request: Promise<Page>, onSuccess?: (page: Page) => void) {
    setBusy(true)
    setActionError(null)
    try {
      const shown = await withText(await request)
      onSuccess?.(shown.page)
      setStatus({ kind: 'done', ...shown })
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

  /** Send the text of the page to the backend and keep the entities it finds. */
  async function recognize(text: string) {
    setRecognizing(true)
    setActionError(null)
    try {
      setRecognized({ text, entities: await recognizeEntities(text) })
    } catch (error) {
      setActionError(errorMessage(error))
    } finally {
      setRecognizing(false)
    }
  }

  /** Set the ready flag of the open page. */
  function markReady(page: Page) {
    return run(updatePage(page.edition_id, page.number, { ready: true }))
  }

  if (status.kind === 'done') {
    const { page, text, textError } = status
    return (
      <PageViewer
        page={page}
        text={text}
        textError={textError}
        busy={busy}
        error={actionError}
        onPrevious={() => goToPage(page.edition_id, page.number - 1)}
        onNext={() => goToPage(page.edition_id, page.number + 1)}
        onMarkReady={() => markReady(page)}
        entities={recognized?.text === text ? recognized.entities : null}
        recognizing={recognizing}
        onRecognizeEntities={() => recognize(text)}
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
