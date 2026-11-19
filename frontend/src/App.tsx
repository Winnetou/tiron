import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  DEFAULT_PAGE_NUMBER,
  addLegalSpelling,
  correctWord,
  getPage,
  markSection,
  pageFromPath,
  pageUrl,
  recognizeEntities,
  spellcheck,
  tagEntity,
  teiToText,
  updatePage,
  type Page,
} from './api'
import { entityKey, type Decision, type Entity } from './entities'
import PageViewer from './PageViewer'
import type { Word } from './segments'
import {
  sectionLabel,
  type SectionKind,
  type TextSelection,
} from './sections'

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** A page together with its text, which is what the viewer shows. */
interface PageWithText {
  page: Page
  text: string
  textError: string | null
  /** Every word of the text and whether it is spelled correctly. */
  words: Word[] | null
  spellError: string | null
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
  let text: string
  try {
    text = await teiToText(page.content)
  } catch (error) {
    return {
      page,
      text: '',
      textError: errorMessage(error),
      words: null,
      spellError: null,
    }
  }
  // The spelling of the text is checked as soon as we have it, so the wrong
  // words are shown right away. The backend finds the words and judges them.
  try {
    return {
      page,
      text,
      textError: null,
      // the edition's legal spellings count as correct
      words: await spellcheck(text, page.edition_id),
      spellError: null,
    }
  } catch (error) {
    return { page, text, textError: null, words: null, spellError: errorMessage(error) }
  }
}

export default function App() {
  // An address that names a page (/editions/{uuid}/pages/{number}) opens it.
  const fromPath = pageFromPath(window.location.pathname)
  const [uuid, setUuid] = useState(fromPath?.editionId ?? '')
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
  // What was done last, to tell the user (not an error): cleared by the next request.
  const [notice, setNotice] = useState<string | null>(null)
  // What the user decided about the entities of a text. Like `recognized`, it
  // belongs to one text and is not used for any other.
  const [decided, setDecided] = useState<{
    text: string
    byKey: Record<string, Decision>
  }>({ text: '', byKey: {} })

  const trimmed = uuid.trim()
  const valid = UUID_PATTERN.test(trimmed)

  /** Open a page of an edition, and put its edition and number in the address. */
  async function open(editionId: string, number: number) {
    setStatus({ kind: 'loading' })
    try {
      const page = await getPage(editionId, number)
      window.history.replaceState(null, '', pageUrl(editionId, number))
      setStatus({ kind: 'done', ...(await withText(page)) })
    } catch (error) {
      setStatus({ kind: 'error', message: errorMessage(error) })
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    // the number of the address, if it is a page of this edition
    const number =
      fromPath?.editionId === trimmed ? fromPath.number : DEFAULT_PAGE_NUMBER
    void open(trimmed, number)
  }

  // Opened with the address of a page: show that page right away.
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current || !fromPath || !UUID_PATTERN.test(fromPath.editionId)) return
    opened.current = true
    void open(fromPath.editionId, fromPath.number)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Run a request for the open page, showing the page it returns. */
  async function run(request: Promise<Page>, onSuccess?: (page: Page) => void) {
    setBusy(true)
    setActionError(null)
    setNotice(null)
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
      window.history.replaceState(null, '', pageUrl(editionId, number))
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

  /** Remember what was decided about an entity of the text on the screen. */
  function decide(text: string, entity: Entity, decision: Decision) {
    setDecided((previous) => ({
      text,
      byKey: {
        ...(previous.text === text ? previous.byKey : {}),
        [entityKey(entity)]: decision,
      },
    }))
  }

  /**
   * Have the backend mark the entity in the XML of the page. The text does not
   * change, so the entities stay as they are; the XML view shows the new tag.
   */
  function confirmEntity(page: Page, text: string, entity: Entity, label: string) {
    return run(tagEntity(page.edition_id, page.number, entity, label), () =>
      decide(text, entity, { kind: 'confirmed', label }),
    )
  }

  /**
   * Have the backend replace a word of the page with `after`: in the XML, where
   * it also records the correction. If the word was misspelled, the new
   * spelling is then also sent as a legal spelling of the edition, so that it is
   * not red afterwards, even if the dictionary does not know it. The text
   * changes, so the page is shown again with its new text, and its spelling is
   * checked again (once both requests are done).
   */
  async function correct(page: Page, word: Word, after: string) {
    setBusy(true)
    setActionError(null)
    try {
      let corrected: Page
      try {
        corrected = await correctWord(page.edition_id, page.number, word, after)
      } catch (error) {
        setActionError(errorMessage(error))
        return
      }
      let legalError: string | null = null
      // (a spelling is one word, so an edit into several words has none, and a
      // removed token is not a spelling)
      if (!word.correct && after !== '' && !/\s/.test(after)) {
        try {
          await addLegalSpelling(page.edition_id, after)
        } catch (error) {
          legalError = errorMessage(error)
        }
      }
      // The word is corrected either way, so that is what is shown.
      setStatus({ kind: 'done', ...(await withText(corrected)) })
      if (legalError) {
        setActionError(
          `The word was corrected, but not saved as a legal spelling: ${legalError}`,
        )
      }
    } finally {
      setBusy(false)
    }
  }

  /**
   * Say that the spelling of a misspelled word is legal in the edition. The text
   * does not change, so only its words are asked for again: the backend counts
   * the spelling as correct now, and the word is no longer red.
   */
  async function legalize(page: Page, text: string, word: Word) {
    setBusy(true)
    setActionError(null)
    try {
      await addLegalSpelling(page.edition_id, word.text)
      let update: Pick<PageWithText, 'words' | 'spellError'>
      try {
        update = {
          words: await spellcheck(text, page.edition_id),
          spellError: null,
        }
      } catch (error) {
        update = { words: null, spellError: errorMessage(error) }
      }
      setStatus((previous) =>
        previous.kind === 'done' ? { ...previous, ...update } : previous,
      )
    } catch (error) {
      setActionError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  /**
   * Have the backend mark the selected text of the page as a section of it (a
   * header, a footer, the page number, the table of contents) in the XML. The
   * page is shown again with its text, which can have changed: the backend
   * splits a paragraph where a table of contents starts or ends in it.
   */
  function markPageSection(
    page: Page,
    selection: TextSelection,
    section: SectionKind,
    n?: string,
  ) {
    return run(markSection(page.edition_id, page.number, selection, section, n), () =>
      setNotice(`Marked as ${sectionLabel(section).toLowerCase()}.`),
    )
  }

  /** Set the ready flag of the open page. */
  function markReady(page: Page) {
    return run(updatePage(page.edition_id, page.number, { ready: true }))
  }

  if (status.kind === 'done') {
    const { page, text, textError, words, spellError } = status
    return (
      <PageViewer
        page={page}
        text={text}
        textError={textError}
        words={words}
        spellError={spellError}
        busy={busy}
        error={actionError}
        onPrevious={() => goToPage(page.edition_id, page.number - 1)}
        onNext={() => goToPage(page.edition_id, page.number + 1)}
        onMarkReady={() => markReady(page)}
        entities={recognized?.text === text ? recognized.entities : null}
        recognizing={recognizing}
        onRecognizeEntities={() => recognize(text)}
        decisions={decided.text === text ? decided.byKey : {}}
        onConfirmEntity={(entity, label) =>
          confirmEntity(page, text, entity, label)
        }
        onRejectEntity={(entity) => decide(text, entity, { kind: 'rejected' })}
        onCorrectWord={(word, after) => correct(page, word, after)}
        onLegalSpelling={(word) => legalize(page, text, word)}
        onMarkSection={(selection, section, n) =>
          markPageSection(page, selection, section, n)
        }
        notice={notice}
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
