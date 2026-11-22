import { useEffect, useRef, useState } from 'react'
import {
  addLegalSpelling,
  addToken,
  correctWord,
  joinLines,
  joinTokens,
  splitLine,
  getPage,
  type PageNumberMatch,
  type TokenMatch,
  markSection,
  pageFromPath,
  pageUrl,
  recognizeEntities,
  spellcheck,
  tagEntity,
  teiEntities,
  teiToText,
  type Page,
} from './api'
import EditionList from './EditionList'
import {
  SimilarPageNumbersReview,
  type PageNumberExample,
} from './EditionWideStructure'
import {
  ApplyOffer,
  SimilarMisspellings,
  type EditionCorrection,
} from './EditionWideCorrection'
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

/**
 * The recognized entities and the ones marked in the XML: where they overlap,
 * the XML wins, as a person marked it. Null while nothing is known (no
 * recognition, and nothing marked).
 */
function withMarked(found: Entity[] | null, marked: Entity[]): Entity[] | null {
  if (found === null && marked.length === 0) return null
  const overlaps = (a: Entity, b: Entity) => a.start < b.end && b.start < a.end
  return [
    ...(found ?? []).filter((entity) => !marked.some((mine) => overlaps(entity, mine))),
    ...marked,
  ]
}

/**
 * Does the correction only put a hyphen after the last word of a line? That is
 * how a line continuation is marked ("abso" to "abso-", with "lutely" on the next
 * line): the word is not wrong anywhere else, so it is not offered to the entire
 * edition. The last word of the page ends a line too. `text` is the text of the
 * page before the correction, in which the word is at its offsets (code points).
 */
function addsLineContinuation(text: string, word: Word, after: string): boolean {
  const next = Array.from(text)[word.end]
  return after === `${word.text}-` && (next === undefined || next === '\n')
}

export default function App() {
  // An address that names a page (/editions/{uuid}/pages/{number}) opens it.
  const fromPath = pageFromPath(window.location.pathname)
  const opens = fromPath !== null && UUID_PATTERN.test(fromPath.editionId)
  const [status, setStatus] = useState<Status>(
    opens ? { kind: 'loading' } : { kind: 'idle' },
  )
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // The entities found in a text, with the text they belong to, so that they
  // are only shown while that same text is on the screen.
  const [recognized, setRecognized] = useState<{
    text: string
    entities: Entity[]
  } | null>(null)
  // The entities that are marked in the XML, as the backend reads them from it,
  // with the XML they are of: they are only shown for that same XML.
  const [marked, setMarked] = useState<{ xml: string; entities: Entity[] } | null>(
    null,
  )
  const [recognizing, setRecognizing] = useState(false)
  // A correction that the user made, which they may apply to the whole edition:
  // first the offer (a small box), then, if they accept it, the list of the places.
  const [offer, setOffer] = useState<EditionCorrection | null>(null)
  const [reviewing, setReviewing] = useState<EditionCorrection | null>(null)
  // The page number of this page, which the user wants to find on the others.
  const [findingPageNumbers, setFindingPageNumbers] =
    useState<PageNumberExample | null>(null)
  // The spellings that were made legal by approving places of the edition, so
  // that each is sent once.
  const legalized = useRef(new Set<string>())
  // What was done last, to tell the user (not an error): cleared by the next request.
  const [notice, setNotice] = useState<string | null>(null)
  // What the user decided about the entities of a text. Like `recognized`, it
  // belongs to one text and is not used for any other.
  const [decided, setDecided] = useState<{
    text: string
    byKey: Record<string, Decision>
  }>({ text: '', byKey: {} })

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

  // Opened with the address of a page: show that page right away.
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current || !fromPath || !opens) return
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

  /** Leave the page for the main page, the list of the editions. */
  function backToEditions() {
    window.history.replaceState(null, '', '/')
    setOffer(null)
    setReviewing(null)
    setFindingPageNumbers(null)
    setStatus({ kind: 'idle' })
  }

  /**
   * Mark the page number on one page of the edition: what is at the offsets is
   * the page number (the same request as marking a section). If it is the page on
   * the screen, it is shown again with its new XML. A refusal rejects, with its
   * reason, for the list to show.
   */
  async function approvePageNumber(editionId: string, match: PageNumberMatch) {
    const updated = await markSection(
      editionId,
      match.page_number,
      { start: match.start, end: match.end, text: match.text },
      'page_number',
    )
    if (
      status.kind === 'done' &&
      status.page.edition_id === editionId &&
      status.page.number === match.page_number
    ) {
      setStatus({ kind: 'done', ...(await withText(updated)) })
    }
  }

  /**
   * Apply the correction of one place of the edition: the token of a page, at
   * its offsets, becomes its suggestion (the same request as the correction of a
   * word), and the suggestion is made a legal spelling of the edition too, so
   * that it is not red afterwards (once for each spelling: it is the same for the
   * next place). If it is a place of the page on the screen, that page is shown
   * again with its new text, after that. A refusal of the correction rejects, with
   * its reason, for the list to show; if only the legal spelling is refused, the
   * place is corrected, and the reason comes back as a warning.
   */
  async function approveMisspelling(
    editionId: string,
    pageNumber: number,
    token: TokenMatch,
  ): Promise<string | undefined> {
    const updated = await correctWord(
      editionId,
      pageNumber,
      { text: token.text, start: token.start, end: token.end, correct: false },
      token.suggestion,
    )
    let warning: string | undefined
    // (a spelling is one word, so a suggestion of several words has none)
    const legal = `${editionId} ${token.suggestion}`
    if (!/\s/.test(token.suggestion) && !legalized.current.has(legal)) {
      try {
        await addLegalSpelling(editionId, token.suggestion)
        legalized.current.add(legal)
      } catch (error) {
        warning = `Corrected, but not saved as a legal spelling: ${errorMessage(error)}`
      }
    }
    if (
      status.kind === 'done' &&
      status.page.edition_id === editionId &&
      status.page.number === pageNumber
    ) {
      setStatus({ kind: 'done', ...(await withText(updated)) })
    }
    return warning
  }

  /** Show another page of the same edition and reflect it in the URL. */
  function goToPage(editionId: string, number: number) {
    return run(getPage(editionId, number), () => {
      window.history.replaceState(null, '', pageUrl(editionId, number))
    })
  }

  /**
   * Ask the backend which entities are marked in the XML, to show them as
   * confirmed. If it cannot say, they are just not shown.
   */
  function loadMarked(xml: string) {
    teiEntities(xml)
      .then((entities) => setMarked({ xml, entities }))
      .catch(() => setMarked(null))
  }

  /**
   * Send the text of the page to the backend and keep the entities it finds,
   * and read the ones that are marked in the XML already: they are shown after
   * a refresh too.
   */
  async function recognize(page: Page, text: string) {
    setRecognizing(true)
    setActionError(null)
    loadMarked(page.content)
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
    return run(tagEntity(page.edition_id, page.number, entity, label), (tagged) => {
      decide(text, entity, { kind: 'confirmed', label })
      loadMarked(tagged.content)
    })
  }

  /**
   * The user marked some text as an entity that the recognition missed: the
   * backend marks it in the XML like any other entity, and it is then read from
   * the XML and shown (confirmed) with the recognized ones.
   */
  function createEntity(page: Page, selection: TextSelection, label: string) {
    const entity: Entity = {
      text: selection.text,
      label,
      start: selection.start,
      end: selection.end,
    }
    return run(tagEntity(page.edition_id, page.number, entity, label), (tagged) =>
      loadMarked(tagged.content),
    )
  }

  /**
   * Have the backend replace a word of the page with `after`: in the XML, where
   * it also records the correction. If the word was misspelled, the new
   * spelling is then also sent as a legal spelling of the edition, so that it is
   * not red afterwards, even if the dictionary does not know it. The text
   * changes, so the page is shown again with its new text, and its spelling is
   * checked again (once both requests are done). Neither a legal spelling nor an
   * offer for the whole edition comes of a hyphen that only marks the word as
   * continued in the next line. `text` is the text of the page as
   * it was shown, in which the word is at its offsets.
   */
  async function correct(page: Page, word: Word, after: string, text: string) {
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
      const continued = addsLineContinuation(text, word, after)
      // (a spelling is one word, so an edit into several words has none, a
      // removed token is not a spelling, and neither is the hyphen that says that
      // a word goes on in the next line)
      if (!word.correct && after !== '' && !/\s/.test(after) && !continued) {
        try {
          await addLegalSpelling(page.edition_id, after)
        } catch (error) {
          legalError = errorMessage(error)
        }
      }
      // The word is corrected either way, so that is what is shown.
      setStatus({ kind: 'done', ...(await withText(corrected)) })
      // A word that became another one may be wrong in other places too (the
      // backend takes the spellings of 1 to 100 characters). Not so for a hyphen
      // that marks the word as continued on the next line.
      if (
        after !== '' &&
        after !== word.text &&
        !continued &&
        word.text.length <= 100 &&
        after.length <= 100
      ) {
        setOffer({
          editionId: page.edition_id,
          incorrect: word.text,
          corrected: after,
        })
      }
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
   * Say that the spelling of a misspelled word is legal in the edition ("legal",
   * not "correct": what was correct in the 19th century often is not anymore, and
   * we have no better word for it). The text does not change, so only its words
   * are asked for again: the backend counts the spelling as correct now, and the
   * word is no longer red.
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

  if (status.kind === 'done') {
    const { page, text, textError, words, spellError } = status
    // What is marked in this page's XML counts as confirmed, whatever was
    // decided: the XML is what a person made.
    const markedHere = marked?.xml === page.content ? marked.entities : []
    const decisionsHere: Record<string, Decision> = {
      ...(decided.text === text ? decided.byKey : {}),
      ...Object.fromEntries(
        markedHere.map((entity) => [
          entityKey(entity),
          { kind: 'confirmed', label: entity.label } as const,
        ]),
      ),
    }
    return (
      <>
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
          onBackToEditions={backToEditions}
          entities={withMarked(
            recognized?.text === text ? recognized.entities : null,
            markedHere,
          )}
          recognizing={recognizing}
          onRecognizeEntities={() => recognize(page, text)}
          decisions={decisionsHere}
          onConfirmEntity={(entity, label) =>
            confirmEntity(page, text, entity, label)
          }
          onRejectEntity={(entity) => decide(text, entity, { kind: 'rejected' })}
          onCorrectWord={(word, after) => correct(page, word, after, text)}
          onLegalSpelling={(word) => legalize(page, text, word)}
          onJoinWords={(words) =>
            run(joinTokens(page.edition_id, page.number, words))
          }
          onSplitLine={(words) =>
            run(splitLine(page.edition_id, page.number, words))
          }
          onJoinLines={(words) =>
            run(joinLines(page.edition_id, page.number, words, text))
          }
          onAddToken={(position, token) =>
            run(addToken(page.edition_id, page.number, position, token))
          }
          onCreateEntity={(selection, label) =>
            createEntity(page, selection, label)
          }
          onMarkSection={(selection, section, n) =>
            markPageSection(page, selection, section, n)
          }
          onFindPageNumbers={() =>
            setFindingPageNumbers({
              editionId: page.edition_id,
              page: page.number,
            })
          }
          notice={notice}
        />
        {offer && !reviewing && (
          <ApplyOffer
            correction={offer}
            onApply={() => {
              setReviewing(offer)
              setOffer(null)
            }}
            onDismiss={() => setOffer(null)}
          />
        )}
        {findingPageNumbers && (
          <SimilarPageNumbersReview
            example={findingPageNumbers}
            onApprove={(match) =>
              approvePageNumber(findingPageNumbers.editionId, match)
            }
            onClose={() => setFindingPageNumbers(null)}
          />
        )}
        {reviewing && (
          <SimilarMisspellings
            correction={reviewing}
            onApprove={(pageNumber, token) =>
              approveMisspelling(reviewing.editionId, pageNumber, token)
            }
            onClose={() => setReviewing(null)}
          />
        )}
      </>
    )
  }

  if (status.kind === 'loading') {
    return (
      <main>
        <p role="status">Loading the page …</p>
      </main>
    )
  }

  if (status.kind === 'error') {
    return (
      <main>
        <p role="alert">{status.message}</p>
        <a href="/">Back to the editions</a>
      </main>
    )
  }

  return (
    <main className="main-page">
      <EditionList />
    </main>
  )
}
