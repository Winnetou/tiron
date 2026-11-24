import { useEffect, useRef, useState } from 'react'
import {
  addLegalSpelling,
  addToken,
  correctWord,
  joinLines,
  joinTokens,
  splitLine,
  ApiError,
  DEFAULT_PAGE_NUMBER,
  LOGIN_REQUIRED,
  getEditionMetadata,
  getPage,
  type EditionMetadata,
  type EntityMatch,
  type MissingHyphen,
  type PageNumberMatch,
  type RubbishLine,
  type TokenMatch,
  markSection,
  pageFromPath,
  pageUrl,
  putHyphensBack,
  removeLine,
  recognizeEntities,
  spellcheck,
  tagEntity,
  teiEntities,
  teiToText,
  type Page,
} from './api'
import EditionList from './EditionList'
import LoginPage from './LoginPage'
import EditionMetadataPanel from './EditionMetadataPanel'
import { PageLoading } from './Loading'
import {
  SimilarEntitiesReview,
  type EntityExample,
} from './EditionWideEntities'
import { SpellingChunkReview } from './EditionWideChunk'
import { TokenReplaceReview } from './EditionWideReplace'
import { RubbishReview, type RubbishEdition } from './EditionWideRubbish'
import { MissingHyphensReview, type HyphenPage } from './MissingHyphens'
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
  // the backend wants a login (no session, or it has run out): the login page
  | { kind: 'login' }
  | { kind: 'loading' }
  | ({ kind: 'done' } & PageWithText)
  // `editionId` is the edition of the address if it is stored (so it can be gone back to)
  | { kind: 'error'; message: string; editionId: string | null }

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
      words: await spellcheck(text, page.edition_id, page.content),
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

/** Whether the backend has the edition stored (its metadata can be had). */
async function editionIsStored(editionId: string): Promise<boolean> {
  try {
    await getEditionMetadata(editionId)
    return true
  } catch {
    return false
  }
}

export default function App() {
  // An address that names a page (/editions/{uuid}/pages/{number}) opens it.
  const fromPath = pageFromPath(window.location.pathname)
  const opens = fromPath !== null && UUID_PATTERN.test(fromPath.editionId)
  // An address of a page whose edition is not a uuid names no edition: say so (and
  // link to the main page), and don't quietly show the main page instead.
  const [status, setStatus] = useState<Status>(
    opens
      ? { kind: 'loading' }
      : fromPath !== null
        ? {
            kind: 'error',
            message: 'Edition not found: the edition in the address is not a valid uuid',
            editionId: null,
          }
        : { kind: 'idle' },
  )
  // What is known of the open page's edition (author, title, place, year): shown above
  // the page number. It is looked for once for an edition, and changes when edited.
  const [metadata, setMetadata] = useState<EditionMetadata | null>(null)
  const openEdition = status.kind === 'done' ? status.page.edition_id : null
  useEffect(() => {
    if (openEdition === null) return
    let current = true
    getEditionMetadata(openEdition)
      .then((found) => current && setMetadata(found))
      // without it the line is just not shown: the page is what matters
      .catch(() => current && setMetadata(null))
    return () => {
      current = false
    }
  }, [openEdition])
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
  // The page whose line ends are looked at for the hyphens that the OCR dropped.
  const [findingHyphens, setFindingHyphens] = useState<HyphenPage | null>(null)
  // The edition whose flagged spellings the user wants to accept by their substring.
  const [acceptingChunk, setAcceptingChunk] = useState<{ editionId: string } | null>(
    null,
  )
  // The edition whose metadata (author, title, place, year) the user is editing.
  const [editingMetadata, setEditingMetadata] = useState<{ editionId: string } | null>(
    null,
  )
  // The edition in which the user wants to replace a token, wherever it is.
  const [replacingToken, setReplacingToken] = useState<{ editionId: string } | null>(
    null,
  )
  // The named entities confirmed on the page, one of which the user wants to find
  // on the other pages of the edition (in all its forms in the cases).
  const [findingEntities, setFindingEntities] = useState<{
    editionId: string
    examples: EntityExample[]
  } | null>(null)
  // The edition whose OCR rubbish lines the user wants to find and remove.
  const [findingRubbish, setFindingRubbish] = useState<RubbishEdition | null>(null)
  // The place of the text that the user points at in its list, to show in the text.
  const [hyphenStretch, setHyphenStretch] = useState<{
    start: number
    end: number
  } | null>(null)
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
      // no session: the login page, and the address stays, so that after the login
      // the same page is opened
      if (error instanceof ApiError && error.status === 401) {
        setStatus({ kind: 'login' })
        return
      }
      // "Not found" is for the page or for the edition: the edition is only gone
      // back to if it is stored (it is, when the page is the one that is missing)
      const gone =
        error instanceof ApiError && error.status === 404
          ? !(await editionIsStored(editionId))
          : false
      setStatus({
        kind: 'error',
        message: errorMessage(error),
        editionId: gone ? null : editionId,
      })
    }
  }

  // Whatever asks for a login (a page that is opened, or an action after the session
  // has run out) is answered with the login page.
  useEffect(() => {
    const needLogin = () => setStatus({ kind: 'login' })
    window.addEventListener(LOGIN_REQUIRED, needLogin)
    return () => window.removeEventListener(LOGIN_REQUIRED, needLogin)
  }, [])

  /** After the login: open the page of the address again, or show the main page. */
  function loggedIn() {
    const fromAddress = pageFromPath(window.location.pathname)
    if (fromAddress && UUID_PATTERN.test(fromAddress.editionId)) {
      void open(fromAddress.editionId, fromAddress.number)
    } else {
      setStatus({ kind: 'idle' })
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
    setFindingHyphens(null)
    setFindingRubbish(null)
    setFindingEntities(null)
    setAcceptingChunk(null)
    setReplacingToken(null)
    setStatus({ kind: 'idle' })
  }

  /**
   * Accept a spelling as legal in an edition (the request of the "legal spelling"
   * button). The page on the screen is checked again, so that the spelling is not
   * flagged there any more. A refusal rejects, with its reason, for the list.
   */
  async function acceptSpelling(editionId: string, spelling: string) {
    await addLegalSpelling(editionId, spelling)
    if (status.kind !== 'done' || status.page.edition_id !== editionId) return
    const { text } = status
    try {
      const words = await spellcheck(text, editionId, status.page.content)
      setStatus((previous) =>
        previous.kind === 'done' && previous.text === text
          ? { ...previous, words, spellError: null }
          : previous,
      )
    } catch (error) {
      setStatus((previous) =>
        previous.kind === 'done' && previous.text === text
          ? { ...previous, words: null, spellError: errorMessage(error) }
          : previous,
      )
    }
  }

  /**
   * Mark a named entity at a place of a page of the edition, with `label` (the same
   * request as confirming an entity). Marking does not change the text. If it is the
   * page on the screen, it is shown again, with the entity as confirmed. A refusal
   * rejects, with its reason, for the list to show.
   */
  async function approveEntity(
    editionId: string,
    pageNumber: number,
    match: EntityMatch,
    label: string,
  ) {
    const updated = await tagEntity(
      editionId,
      pageNumber,
      { text: match.text, label, start: match.start, end: match.end },
      label,
    )
    if (
      status.kind === 'done' &&
      status.page.edition_id === editionId &&
      status.page.number === pageNumber
    ) {
      loadMarked(updated.content)
      setStatus({ kind: 'done', ...(await withText(updated)) })
    }
  }

  /**
   * Remove one line of a page of the edition (OCR rubbish). If it is the page on
   * the screen, it is shown again with its new text. Resolves with how many
   * characters of the text went, for the list to move the lines after it by. A
   * refusal rejects, with its reason, for the list to show.
   */
  async function approveRubbish(
    editionId: string,
    pageNumber: number,
    line: RubbishLine,
  ): Promise<number> {
    const { page: updated, removed } = await removeLine(editionId, pageNumber, line)
    if (
      status.kind === 'done' &&
      status.page.edition_id === editionId &&
      status.page.number === pageNumber
    ) {
      setStatus({ kind: 'done', ...(await withText(updated)) })
    }
    return removed
  }

  /**
   * Put the hyphens back at some line ends of a page (one request, all or none).
   * The page on the screen is shown again with its new text. A refusal rejects,
   * with its reason, for the list to show.
   */
  async function approveHyphens(page: HyphenPage, hyphens: MissingHyphen[]) {
    const updated = await putHyphensBack(page.editionId, page.page, hyphens)
    if (
      status.kind === 'done' &&
      status.page.edition_id === page.editionId &&
      status.page.number === page.page
    ) {
      setStatus({ kind: 'done', ...(await withText(updated)) })
    }
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
    token: Pick<TokenMatch, 'text' | 'start' | 'end' | 'suggestion'>,
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
    setFindingHyphens(null) // its places are on the page that is left
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
          words: await spellcheck(text, page.edition_id, page.content),
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
    lang?: string,
  ) {
    return run(
      markSection(page.edition_id, page.number, selection, section, n, lang),
      () =>
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
          metadata={metadata?.id === page.edition_id ? metadata : null}
          onEditMetadata={() => setEditingMetadata({ editionId: page.edition_id })}
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
          onMarkSection={(selection, section, n, lang) =>
            markPageSection(page, selection, section, n, lang)
          }
          onFindPageNumbers={() =>
            setFindingPageNumbers({
              editionId: page.edition_id,
              page: page.number,
            })
          }
          onReplaceToken={() => setReplacingToken({ editionId: page.edition_id })}
          onApplyRule={() => setAcceptingChunk({ editionId: page.edition_id })}
          onFindEntities={(examples) =>
            setFindingEntities({ editionId: page.edition_id, examples })
          }
          onFindRubbish={() => setFindingRubbish({ editionId: page.edition_id })}
          onFindHyphens={() =>
            setFindingHyphens({ editionId: page.edition_id, page: page.number })
          }
          highlight={hyphenStretch}
          dock={
            // docked under the text, not over the page: the text and the scan
            // stay in sight while the places are checked
            findingHyphens &&
            findingHyphens.editionId === page.edition_id &&
            findingHyphens.page === page.number ? (
              <MissingHyphensReview
                page={findingHyphens}
                onApprove={(hyphens) => approveHyphens(findingHyphens, hyphens)}
                onPoint={setHyphenStretch}
                onClose={() => setFindingHyphens(null)}
              />
            ) : null
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
        {editingMetadata && (
          <EditionMetadataPanel
            editionId={editingMetadata.editionId}
            onSaved={setMetadata}
            onClose={() => setEditingMetadata(null)}
          />
        )}
        {replacingToken && (
          <TokenReplaceReview
            editionId={replacingToken.editionId}
            onApprove={(pageNumber, token) =>
              approveMisspelling(replacingToken.editionId, pageNumber, token)
            }
            onClose={() => setReplacingToken(null)}
          />
        )}
        {acceptingChunk && (
          <SpellingChunkReview
            editionId={acceptingChunk.editionId}
            onAccept={(spelling) =>
              acceptSpelling(acceptingChunk.editionId, spelling)
            }
            onClose={() => setAcceptingChunk(null)}
          />
        )}
        {findingEntities && (
          <SimilarEntitiesReview
            editionId={findingEntities.editionId}
            examples={findingEntities.examples}
            onApprove={(pageNumber, match, label) =>
              approveEntity(findingEntities.editionId, pageNumber, match, label)
            }
            onClose={() => setFindingEntities(null)}
          />
        )}
        {findingRubbish && (
          <RubbishReview
            edition={findingRubbish}
            onApprove={(pageNumber, line) =>
              approveRubbish(findingRubbish.editionId, pageNumber, line)
            }
            onClose={() => setFindingRubbish(null)}
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

  if (status.kind === 'login') return <LoginPage onLoggedIn={loggedIn} />

  if (status.kind === 'loading') {
    return (
      <main>
        <PageLoading>Loading the page …</PageLoading>
      </main>
    )
  }

  if (status.kind === 'error') {
    // the way back: the edition of the address (its first page) if it is stored,
    // otherwise the main page
    const way =
      status.editionId === null
        ? { href: '/', label: 'Back to the list of editions' }
        : {
            href: pageUrl(status.editionId, DEFAULT_PAGE_NUMBER),
            label: 'Back to the edition',
          }
    return (
      <main className="error-page review-styled">
        {/* the skeleton points its finger at the message */}
        <img className="error-page-image" src="/vademecum.jpg" alt="" />
        <div className="error-page-text">
          <p role="alert" className="error-page-message">
            {status.message}
          </p>
          <button type="button" onClick={() => window.location.assign(way.href)}>
            {way.label}
          </button>
        </div>
      </main>
    )
  }

  return (
    <main className="main-page">
      <EditionList />
    </main>
  )
}
