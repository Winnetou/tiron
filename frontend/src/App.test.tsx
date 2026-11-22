import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  MissingHyphen,
  Page,
  PageMatches,
  SectionSpan,
  SimilarPageNumbers,
} from './api'
import App from './App'
import type { Entity } from './entities'
import './index.css'
import type { Word } from './segments'

const UUID = '4a2cfdf3-006a-4613-9aee-42b300be604b'
const SCAN = 'https://polona.pl/iiif/3/x/full/max/0/default.jpg'

/** The page content is TEI XML, as the backend returns it. */
const xmlOf = (text: string) =>
  `<?xml version="1.0"?><TEI><teiHeader>HEADER</teiHeader><text><p>${text}</p></text></TEI>`
/** What the backend's POST /tei/text answers for such XML. */
const textOf = (xml: string) =>
  xml.match(/<p>([\s\S]*)<\/p>/)![1].replace(/<[^>]+>/g, '') // tags are not text, and the text may have several lines

/**
 * What the backend's POST /tei/entities answers for such XML: the elements
 * <persName>, <placeName>, ... with their place in the text.
 */
const entitiesOf = (xml: string): Entity[] => {
  const entities: Entity[] = []
  let offset = 0
  const content = xml.match(/<p>([\s\S]*)<\/p>/)![1]
  const plain = [...content.replace(/<[^>]+>/g, '')]
  const open: { label: string; start: number }[] = []
  for (const token of content.split(/(<[^>]+>)/)) {
    const tag = token.match(/^<(\/?)(persName|placeName|orgName|geogName|date|time|ref|bibl|cit)\b/)
    if (tag && !tag[1]) open.push({ label: tag[2], start: offset })
    else if (tag) {
      const { label, start } = open.pop()!
      entities.push({
        label,
        start,
        end: offset,
        text: plain.slice(start, offset).join(''),
      })
    } else if (!token.startsWith('<')) offset += [...token].length
  }
  return entities.sort((a, b) => a.start - b.start)
}

/** What the backend does for POST .../tokens, for a text without markup. */
const insertToken = (xml: string, position: number, token: string) => {
  const text = [...textOf(xml)]
  const lead = position === 0 || /\s/.test(text[position - 1]) ? '' : ' '
  const trail = position >= text.length || /\s/.test(text[position]) ? '' : ' '
  text.splice(position, 0, lead + token + trail)
  return xmlOf(text.join(''))
}

const PAGE: Page = {
  id: 'a8ae3c6d-c4cf-4d11-82d0-112582bb355f',
  edition_id: UUID,
  number: 0,
  scan_uri: SCAN,
  ready: false,
  content: xmlOf('Ala ma kota'),
}

interface SectionBody {
  start: number
  end: number
  text: string
  section: string
}

interface TagBody {
  start: number
  end: number
  text: string
  label: string
}

interface CorrectionBody {
  start: number
  end: number
  before: string
  after: string
}

interface Call {
  method: string
  url: string
  body?: unknown
}

interface Options {
  /** Pages by number; defaults to a page 0..3 whose text is "text of page N". */
  page?: (number: number) => Partial<Page> | Error
  /** Answer of POST /tei/text; defaults to the text between <p> and </p>. */
  tei?: (xml: string) => { text: string } | Error
  /** Answer of GET /editions?page=N; defaults to 250 editions. */
  editions?: (page: number) => { items: string[]; pages: number } | Error
  /** Answer of POST /tei/entities; defaults to the entities marked in the XML. */
  marked?: (xml: string) => Entity[] | Error
  /** Answer of POST .../tokens; defaults to putting the token in the text. */
  token?: (body: { position: number; text: string }, page: Page) => Partial<Page> | Error
  /** Answer of POST .../joins; defaults to removing the spaces from the text. */
  join?: (
    body: { start: number; end: number; text: string },
    page: Page,
  ) => Partial<Page> | Error
  /** Answer of POST .../line-splits; defaults to a line break at the space. */
  split?: (
    body: { start: number; end: number; text: string },
    page: Page,
  ) => Partial<Page> | Error
  /** Answer of POST .../line-joins; defaults to a space instead of the line break. */
  joinLines?: (
    body: { start: number; end: number; text: string },
    page: Page,
  ) => Partial<Page> | Error
  /** Answer of GET .../similar-page-numbers for a page; defaults to a refusal. */
  similarNumbers?: (page: number) => SimilarPageNumbers | Error
  /** Answer of GET .../missing-hyphens for a page; defaults to none. */
  hyphens?: (page: number) => MissingHyphen[] | Error
  /** Answer of POST .../hyphens; defaults to replacing each word in the XML. */
  putHyphens?: (
    body: { fixes: { start: number; end: number; before: string; after: string }[] },
    page: Page,
  ) => Partial<Page> | Error
  /** Answer of GET .../similar-misspellings; defaults to no place. */
  similar?: (query: {
    incorrect_spelling: string
    corrected_spelling: string
  }) => PageMatches[] | Error
  /** Answer of PATCH; defaults to applying the changes. */
  patch?: Error
  /** Answer of POST /ner; defaults to no entities. */
  ner?: (text: string) => Entity[] | Error
  /** Answer of POST /spellcheck; defaults to no words (so no clickable words). */
  spell?: (text: string) => Word[] | Error
  /** Answer of POST .../legal-spellings; an Error to refuse it. */
  legal?: (body: { spelling: string }) => Error | undefined
  /**
   * Answer of POST .../corrections: the changes to the page. Defaults to what
   * the backend does: the word is replaced in the XML.
   */
  correct?: (body: CorrectionBody, page: Page) => Partial<Page> | Error
  /**
   * Answer of POST .../entities: the changes to the page. Defaults to what the
   * backend does: the entity's text gets a `label` element around it in the XML.
   */
  tag?: (body: TagBody, page: Page) => Partial<Page> | Error
  /**
   * Answer of POST .../sections: the changes to the page. Defaults to what the
   * backend does for a header, a footer or a page number: the text gets an
   * element around it in the XML.
   */
  section?: (body: SectionBody, page: Page) => Partial<Page> | Error
  /** Answer of POST /tei/sections for the XML: the marked sections (default none). */
  sections?: (xml: string) => SectionSpan[] | Error
}

const reply = (ok: boolean, status: number, body: unknown) => ({
  ok,
  status,
  statusText: ok ? 'OK' : 'Error',
  json: async () => body,
})

const failure = (status: number, error: Error) =>
  reply(false, status, { detail: error.message })

let calls: Call[]

/** A fake backend behind fetch: pages, PATCH and POST /tei/text. */
function fakeBackend(options: Options = {}) {
  calls = []
  const pages = new Map<number, Page>()
  const legalSpellings = new Set<string>()
  const pageFor = (number: number): Page | Error => {
    if (number < 0 || number > 3) return new Error('Page not found')
    const given = options.page?.(number)
    if (given instanceof Error) return given
    return (
      pages.get(number) ?? {
        ...PAGE,
        number,
        content: xmlOf(`text of page ${number}`),
        ...given,
      }
    )
  }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, body })
    if (url.startsWith('/editions?page=')) {
      const page = Number(url.split('=')[1])
      const answer = options.editions
        ? options.editions(page)
        : { items: editionIds(page), pages: 3 }
      return answer instanceof Error
        ? failure(500, answer)
        : reply(true, 200, { total: 250, page, size: 100, ...answer })
    }
    if (url.includes('/similar-misspellings?')) {
      const query = Object.fromEntries(new URLSearchParams(url.split('?')[1]))
      const answer = options.similar ? options.similar(query as never) : []
      return answer instanceof Error ? failure(404, answer) : reply(true, 200, answer)
    }
    if (url === '/spellcheck') {
      const answer = options.spell ? options.spell(body.text) : []
      // like the backend: the legal spellings of the edition count as correct,
      // and so do their capital forms
      return answer instanceof Error
        ? failure(503, answer)
        : reply(true, 200, {
            words: answer.map((word) =>
              legalSpellings.has(word.text) ||
              legalSpellings.has(word.text.toLowerCase())
                ? { ...word, correct: true }
                : word,
            ),
          })
    }
    if (url.endsWith('/legal-spellings')) {
      const refused = options.legal?.(body)
      if (refused) return failure(404, refused)
      legalSpellings.add(body.spelling)
      return reply(true, 201, {
        edition_id: url.split('/')[2],
        spelling: body.spelling,
      })
    }
    if (url === '/ner') {
      const answer = options.ner ? options.ner(body.text) : []
      return answer instanceof Error
        ? failure(503, answer)
        : reply(true, 200, { entities: answer })
    }
    if (url === '/tei/text') {
      const answer = options.tei
        ? options.tei(body.xml)
        : { text: textOf(body.xml) }
      return answer instanceof Error ? failure(422, answer) : reply(true, 200, answer)
    }
    if (url === '/tei/entities') {
      const answer = options.marked ? options.marked(body.xml) : entitiesOf(body.xml)
      return answer instanceof Error
        ? failure(422, answer)
        : reply(true, 200, { entities: answer })
    }
    if (url === '/tei/sections') {
      const answer = options.sections ? options.sections(body.xml) : []
      return answer instanceof Error
        ? failure(422, answer)
        : reply(true, 200, { sections: answer })
    }
    const numbering = url.match(/\/pages\/(\d+)\/similar-page-numbers$/)
    if (numbering) {
      const answer = options.similarNumbers
        ? options.similarNumbers(Number(numbering[1]))
        : new Error('No page number is marked on this page')
      return answer instanceof Error ? failure(422, answer) : reply(true, 200, answer)
    }
    const missing = url.match(/\/pages\/(\d+)\/missing-hyphens$/)
    if (missing) {
      const answer = options.hyphens ? options.hyphens(Number(missing[1])) : []
      return answer instanceof Error ? failure(422, answer) : reply(true, 200, answer)
    }
    const hyphening = url.match(/\/pages\/(\d+)\/hyphens$/)
    if (hyphening) {
      const number = Number(hyphening[1])
      const page = pageFor(number)
      if (page instanceof Error) return failure(404, page)
      const current = pages.get(number) ?? page
      const changes = options.putHyphens
        ? options.putHyphens(body, current)
        : {
            content: [...body.fixes]
              .reverse()
              .reduce(
                (xml: string, fix: { before: string; after: string }) =>
                  xml.replace(fix.before, fix.after),
                current.content,
              ),
          }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    const tagging = url.match(/\/pages\/(\d+)\/entities$/)
    const correcting = url.match(/\/pages\/(\d+)\/corrections$/)
    const sectioning = url.match(/\/pages\/(\d+)\/sections$/)
    const adding = url.match(/\/pages\/(\d+)\/tokens$/)
    const joining = url.match(/\/pages\/(\d+)\/joins$/)
    const splitting = url.match(/\/pages\/(\d+)\/line-splits$/)
    const lineJoining = url.match(/\/pages\/(\d+)\/line-joins$/)
    const number = Number(
      tagging?.[1] ??
        correcting?.[1] ??
        sectioning?.[1] ??
        adding?.[1] ??
        joining?.[1] ??
        splitting?.[1] ??
        lineJoining?.[1] ??
        url.split('/').pop(),
    )
    const page = pageFor(number)
    if (page instanceof Error) return failure(404, page)
    if (correcting) {
      const current = pages.get(number) ?? page
      const changes = options.correct
        ? options.correct(body, current)
        : { content: current.content.replace(body.before, body.after) }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (joining) {
      const current = pages.get(number) ?? page
      const changes = options.join
        ? options.join(body, current)
        : { content: current.content.replace(body.text, body.text.replace(/ /g, '')) }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (lineJoining) {
      const current = pages.get(number) ?? page
      const changes = options.joinLines
        ? options.joinLines(body, current)
        : {
            // an empty line is reduced to a line break, a line break to a space
            content: current.content.replace(
              body.text,
              body.text.includes('\n\n')
                ? body.text.replace('\n\n', '\n')
                : body.text.replace('\n', ' '),
            ),
          }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (splitting) {
      const current = pages.get(number) ?? page
      const changes = options.split
        ? options.split(body, current)
        : { content: current.content.replace(body.text, body.text.replace(' ', '\n')) }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (adding) {
      const current = pages.get(number) ?? page
      const changes = options.token
        ? options.token(body, current)
        : { content: insertToken(current.content, body.position, body.text) }
      if (changes instanceof Error) return failure(422, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (sectioning) {
      const current = pages.get(number) ?? page
      const changes = options.section
        ? options.section(body, current)
        : {
            content: current.content.replace(
              body.text,
              `<${body.section}>${body.text}</${body.section}>`,
            ),
          }
      if (changes instanceof Error) return failure(422, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (tagging) {
      const current = pages.get(number) ?? page
      const changes = options.tag
        ? options.tag(body, current)
        : {
            content: current.content.replace(
              body.text,
              `<${body.label}>${body.text}</${body.label}>`,
            ),
          }
      if (changes instanceof Error) return failure(409, changes)
      const updated = { ...current, ...changes }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    if (method === 'PATCH') {
      if (options.patch) return failure(404, options.patch)
      const updated = { ...page, ...body }
      pages.set(number, updated)
      return reply(true, 200, updated)
    }
    return reply(true, 200, page)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The ids of the editions on a page of the list: 100 on a page, the first is UUID. */
const editionIds = (page: number) =>
  Array.from({ length: page === 3 ? 50 : 100 }, (_, i) => {
    const n = (page - 1) * 100 + i
    return n === 0 ? UUID : `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
  })

/**
 * Open a page: the browser is at the page's address
 * (/editions/{uuid}/pages/{number}), which opens it by itself.
 */
async function openPage(options?: Options, number = 0) {
  window.history.pushState({}, '', `/editions/${UUID}/pages/${number}`)
  const fetchMock = fakeBackend(options)
  render(<App />)
  return fetchMock
}

/** Open the main page, at `search` (for example "?page=2"). */
function openMainPage(options?: Options, search = '') {
  window.history.pushState({}, '', `/${search}`)
  const fetchMock = fakeBackend(options)
  render(<App />)
  return fetchMock
}

const pageText = () => screen.getByRole('region', { name: 'Page text' })
const calledUrls = () => calls.map((c) => `${c.method} ${c.url}`)

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

describe('the page address', () => {
  it('requests page 0 of the edition', async () => {
    const fetchMock = await openPage()

    await screen.findByRole('region', { name: 'Page text' })
    expect(fetchMock).toHaveBeenCalledWith(`/editions/${UUID}/pages/0`)
  })

  it('opens the page that the address names', async () => {
    await openPage({}, 2)

    await waitFor(() => expect(pageText().textContent).toBe('text of page 2'))
    expect(calledUrls()[0]).toBe(`GET /editions/${UUID}/pages/2`)
    expect(calledUrls().filter((u) => u.startsWith('GET'))).toHaveLength(1)
    expect(window.location.pathname).toBe(`/editions/${UUID}/pages/2`)
  })

  it('shows the backend error detail when the page cannot be loaded', async () => {
    await openPage({ page: () => new Error('Edition not found') })

    expect(await screen.findByRole('alert')).toHaveTextContent('Edition not found')
    expect(calledUrls()).toEqual([`GET /editions/${UUID}/pages/0`]) // no TEI call
  })

  it('links back to the main page when the page cannot be loaded', async () => {
    await openPage({ page: () => new Error('Page not found') })

    await screen.findByRole('alert')
    expect(screen.getByRole('link', { name: 'Back to the editions' })).toHaveAttribute(
      'href',
      '/',
    )
  })

  it('shows an error when the request fails', async () => {
    window.history.pushState({}, '', `/editions/${UUID}/pages/0`)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network down')))
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Network down')
  })

  it('shows the main page for an address that is not a page', async () => {
    window.history.pushState({}, '', '/editions/not-a-uuid/pages/3')
    fakeBackend()
    render(<App />)

    expect(await screen.findByRole('navigation', { name: 'Editions' })).toBeInTheDocument()
  })
})

describe('the main page', () => {
  const links = () =>
    within(screen.getByRole('navigation', { name: 'Editions' })).getAllByRole('link')

  it('has no field for a uuid', async () => {
    openMainPage()

    await screen.findByRole('navigation', { name: 'Editions' })
    expect(screen.queryByLabelText('Edition UUID')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('shows the first 100 editions as links', async () => {
    openMainPage()

    await screen.findByRole('navigation', { name: 'Editions' })
    expect(calledUrls()).toEqual(['GET /editions?page=1'])
    expect(links()).toHaveLength(100)
    expect(links().map((a) => a.textContent)).toEqual(editionIds(1))
  })

  it('links each edition to its first page', async () => {
    openMainPage()

    await screen.findByRole('navigation', { name: 'Editions' })
    expect(links()[0]).toHaveAttribute('href', `/editions/${UUID}/pages/0`)
    expect(links()[1]).toHaveAttribute('href', `/editions/${editionIds(1)[1]}/pages/0`)
  })

  it('moves between the pages of the list', async () => {
    openMainPage()
    await screen.findByRole('navigation', { name: 'Editions' })
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() => expect(screen.getByText('Page 2 of 3')).toBeInTheDocument())
    expect(links().map((a) => a.textContent)).toEqual(editionIds(2))
    expect(window.location.search).toBe('?page=2')

    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))

    await waitFor(() => expect(screen.getByText('Page 1 of 3')).toBeInTheDocument())
    expect(window.location.search).toBe('')
  })

  it('opens on the page that the address names', async () => {
    openMainPage({}, '?page=3')

    await screen.findByText('Page 3 of 3')
    expect(calledUrls()).toEqual(['GET /editions?page=3'])
    expect(links()).toHaveLength(50)
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('shows the backend error', async () => {
    openMainPage({ editions: () => new Error('Database down') })

    expect(await screen.findByRole('alert')).toHaveTextContent('Database down')
  })
})

describe('text from the TEI XML', () => {
  it('sends the page content (XML) to the backend and shows the text it returns', async () => {
    await openPage()

    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    expect(calledUrls()).toEqual([
      `GET /editions/${UUID}/pages/0`,
      'POST /tei/text',
      'POST /spellcheck',
    ])
    expect(calls[1].body).toEqual({ xml: xmlOf('text of page 0') })
  })

  it('never shows the XML', async () => {
    await openPage()
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    expect(document.body.textContent).not.toContain('<TEI>')
    expect(document.body.textContent).not.toContain('HEADER')
    expect(screen.queryByDisplayValue(/<TEI>/)).not.toBeInTheDocument()
  })

  it('shows whatever text the backend says, not its own reading of the XML', async () => {
    await openPage({ tei: () => ({ text: 'extracted by the backend' }) })

    await waitFor(() => expect(pageText().textContent).toBe('extracted by the backend'))
  })

  it('shows the backend reason, and no text, when the XML is rejected', async () => {
    await openPage({
      tei: () => new Error('The text is not well-formed XML (line 1, column 4)'),
    })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The text of this page cannot be shown: The text is not well-formed XML (line 1, column 4)',
    )
    expect(pageText().textContent).toBe('')
    // the rest of the page is still usable
    expect(screen.getByRole('img')).toHaveAttribute('src', SCAN)
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
  })

  it('shows an error when the text request itself fails', async () => {
    const fetchMock = fakeBackend()
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/tei/text') throw new Error('Network down')
      return reply(true, 200, PAGE)
    })
    window.history.pushState({}, '', `/editions/${UUID}/pages/0`)
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Network down')
    expect(pageText().textContent).toBe('')
  })

  it('shows the text read-only: there is nothing to edit or save', async () => {
    await openPage()
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    expect(screen.queryByRole('textbox', { name: 'Page text' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    await userEvent.type(pageText(), 'x')
    expect(pageText().textContent).toBe('text of page 0')
  })
})

describe('named entity recognition', () => {
  const entity = (text: string, start: number, label = 'persName'): Entity => ({
    text,
    label,
    start,
    end: start + [...text].length,
  })
  const nerButton = () =>
    screen.getByRole('button', { name: 'Named entity recognition' })
  const marks = () =>
    Array.from(pageText().querySelectorAll('mark')).map((m) => m.textContent)

  /** Open a page whose text is `text`, with the entities the backend will find. */
  async function openWithText(
    text: string,
    entities: Entity[] | Error = [],
    extra: Options = {},
  ) {
    await openPage({
      page: (n) => ({ content: xmlOf(text), number: n }),
      ner: () => entities,
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe(text))
  }
  const nerCalls = () => calls.filter((c) => c.url === '/ner')

  it('sends the text of the page, not the XML, when the button is clicked', async () => {
    await openWithText('Adam Mickiewicz mieszkał w Paryżu')

    await userEvent.click(nerButton())

    await waitFor(() => expect(nerCalls()).toHaveLength(1))
    expect(nerCalls()[0].method).toBe('POST')
    expect(nerCalls()[0].body).toEqual({ text: 'Adam Mickiewicz mieszkał w Paryżu' })
  })

  it('does nothing until the button is clicked', async () => {
    await openWithText('Adam Mickiewicz mieszkał w Paryżu', [entity('Adam Mickiewicz', 0)])

    expect(nerCalls()).toHaveLength(0)
    expect(marks()).toEqual([])
  })

  it('highlights the recognized entities', async () => {
    const text = 'Adam Mickiewicz mieszkał w Paryżu.'
    await openWithText(text, [
      entity('Adam Mickiewicz', 0),
      entity('Paryżu', 27, 'placeName'),
    ])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Adam Mickiewicz', 'Paryżu']))
    expect(pageText().textContent).toBe(text) // the text itself is unchanged
    // the label is not a native tooltip any more: it is in the dialog
    expect(pageText().querySelector('mark')).not.toHaveAttribute('title')
  })

  it('highlights in yellow, in a rounded border', async () => {
    await openWithText('Adam Mickiewicz', [entity('Adam Mickiewicz', 0)])

    await userEvent.click(nerButton())

    const mark = await waitFor(() => {
      const found = pageText().querySelector('mark')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    const style = getComputedStyle(mark)
    const [r, g, b] = style.backgroundColor.match(/\d+/g)!.map(Number)
    expect(r).toBeGreaterThan(200) // yellow: red and green high, blue low
    expect(g).toBeGreaterThan(200)
    expect(b).toBeLessThan(r - 30)
    expect(style.borderStyle).toBe('solid')
    expect(parseFloat(style.borderRadius)).toBeGreaterThan(0)
    // ...and the text around it is not styled like it
    expect(getComputedStyle(pageText()).color).not.toBe(style.color)
  })

  it('marks only the stretch the backend pointed at, not every equal word', async () => {
    // "Ala" is an entity only the second time
    const text = 'Ala ma kota, a Ala nie'
    await openWithText(text, [entity('Ala', text.lastIndexOf('Ala'))])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Ala']))
    const wrap = pageText().querySelector('mark')!.parentElement!
    expect(wrap.previousSibling?.textContent).toBe('Ala ma kota, a ')
    expect(wrap.nextSibling?.textContent).toBe(' nie')
  })

  it('counts offsets in code points, as the backend does, after emoji', async () => {
    const text = '😀😀 Jan Kowalski'
    // the backend counts 😀 as one character each: "Jan Kowalski" starts at 3
    await openWithText(text, [entity('Jan Kowalski', 3)])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
    expect(pageText().textContent).toBe(text)
  })

  it('skips an entity that does not match the text instead of guessing', async () => {
    await openWithText('Ala ma kota', [
      entity('Jan', 0), // the text there is "Ala"
      entity('kota', 7),
      entity('x', 500), // outside the text
    ])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['kota']))
  })

  it('shows no count of the entities', async () => {
    await openWithText('Jan i Ewa', [entity('Jan', 0), entity('Ewa', 6)])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Jan', 'Ewa']))
    expect(screen.queryByText(/\d+ entities/)).toBeNull()
  })

  it('says when there are none', async () => {
    await openWithText('Ala ma kota', [])

    await userEvent.click(nerButton())

    await waitFor(() => expect(nerCalls()).toHaveLength(1))
    expect(marks()).toEqual([])
  })

  it('sends a new request on every click', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])

    await userEvent.click(nerButton())
    await waitFor(() => expect(nerCalls()).toHaveLength(1))
    await userEvent.click(nerButton())

    await waitFor(() => expect(nerCalls()).toHaveLength(2))
    expect(marks()).toEqual(['Jan Kowalski'])
  })

  it('is busy, and cannot be clicked again, while the request is running', async () => {
    let finish: (entities: Entity[]) => void = () => {}
    await openPage({
      page: () => ({ content: xmlOf('Jan Kowalski') }),
      ner: () => {
        throw new Error('replaced below')
      },
    })
    await waitFor(() => expect(pageText().textContent).toBe('Jan Kowalski'))
    const original = (
      fetch as unknown as { getMockImplementation: () => (
        url: string,
        init?: RequestInit,
      ) => Promise<unknown> }
    ).getMockImplementation()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url !== '/ner') return original(url, init)
        const entities = await new Promise<Entity[]>((resolve) => (finish = resolve))
        return reply(true, 200, { entities })
      }),
    )

    await userEvent.click(nerButton())

    expect(nerButton()).toBeDisabled()
    expect(nerButton()).toHaveAttribute('aria-busy', 'true')
    finish([entity('Jan Kowalski', 0)])
    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
    expect(nerButton()).toBeEnabled()
  })

  it('shows the backend error and keeps the text as it was', async () => {
    await openWithText('Jan Kowalski', new Error('The spaCy model is not installed'))

    await userEvent.click(nerButton())

    expect(await screen.findByRole('alert')).toHaveTextContent('not installed')
    expect(pageText().textContent).toBe('Jan Kowalski')
    expect(marks()).toEqual([])
    expect(nerButton()).toBeEnabled()
  })

  it('shows the entities in the text', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
  })

  it('clears the highlights when another page is shown', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)], {
      // the next page has another text
      page: (n) => ({ content: xmlOf(n === 0 ? 'Jan Kowalski' : 'Inny tekst') }),
    })
    await userEvent.click(nerButton())
    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() => expect(screen.getByText('Page 1')).toBeInTheDocument())
    expect(marks()).toEqual([])
    expect(screen.queryByText(/entit/)).toBeNull()
  })

  it('cannot be used when there is no text', async () => {
    await openPage({ tei: () => new Error('The XML is empty') })
    await screen.findByRole('alert')

    expect(nerButton()).toBeDisabled()
    expect(nerCalls()).toHaveLength(0)
  })

  it('leaves the Correction button as a stub', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])
    const before = calls.length

    await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

    expect(nerCalls()).toHaveLength(0)
    expect(calls).toHaveLength(before) // no request
    expect(pageText().textContent).toBe('Jan Kowalski')
  })
})

describe('confirming or rejecting an entity', () => {
  const entity = (text: string, start: number, label = 'persName'): Entity => ({
    text,
    label,
    start,
    end: start + [...text].length,
  })
  const TEXT = 'Adam Mickiewicz mieszkał w Paryżu od 1840 roku.'
  const at = (text: string) => TEXT.indexOf(text)
  const ENTITIES = [
    entity('Adam Mickiewicz', at('Adam Mickiewicz')),
    entity('Paryżu', at('Paryżu'), 'placeName'),
    entity('1840 roku', at('1840 roku'), 'date'),
  ]
  const mark = (text: string) =>
    screen.getByText(text, { selector: 'mark' }) as HTMLElement
  const dialog = () => screen.queryByRole('dialog')
  const tagCalls = () => calls.filter((c) => /\/pages\/\d+\/entities$/.test(c.url))
  const hover = (text: string) => userEvent.hover(mark(text))

  async function recognized(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      ner: () => ENTITIES,
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))
    await userEvent.click(
      screen.getByRole('button', { name: 'Named entity recognition' }),
    )
    await waitFor(() =>
      expect(pageText().querySelectorAll('mark').length).toBeGreaterThan(0),
    )
  }

  describe('where the dialog appears', () => {
    // jsdom has no layout: the page box is 400px high from y=100, and each
    // entity is where `at` says.
    const BOX = { top: 100, bottom: 500, left: 0, right: 300, width: 300, height: 400 }
    let at: Record<string, number> = {}
    const original = Element.prototype.getBoundingClientRect

    beforeEach(() => {
      at = {}
      Element.prototype.getBoundingClientRect = function (this: Element) {
        if (this.classList.contains('page-box')) return BOX as DOMRect
        const top = this.classList.contains('entity-wrap')
          ? at[this.textContent ?? '']
          : undefined
        if (top === undefined) return original.call(this)
        return { ...BOX, top, bottom: top + 24, height: 24 } as DOMRect
      }
    })
    afterEach(() => {
      Element.prototype.getBoundingClientRect = original
    })

    it('is below an entity in the upper half of the page', async () => {
      await recognized()
      at['Adam Mickiewicz'] = 120

      await hover('Adam Mickiewicz')

      expect(dialog()).not.toHaveClass('above')
    })

    it('is above an entity in the lower half, so that it stays in sight', async () => {
      await recognized()
      at['Paryżu'] = 440

      await hover('Paryżu')

      expect(dialog()).toHaveClass('above')
      expect(within(dialog()!).getByRole('button', { name: 'Confirm' })).toBeEnabled()
    })

    it('is placed again each time, by where the entity is', async () => {
      await recognized()
      at['Paryżu'] = 440
      at['Adam Mickiewicz'] = 120

      await hover('Paryżu')
      expect(dialog()).toHaveClass('above')
      await userEvent.unhover(mark('Paryżu'))
      await hover('Adam Mickiewicz')

      expect(dialog()).not.toHaveClass('above')
    })

    it('is above also when opened with the keyboard', async () => {
      await recognized()
      at['Paryżu'] = 440

      mark('Paryżu').focus()

      await waitFor(() => expect(dialog()).toHaveClass('above'))
    })
  })

  describe('the dialog', () => {
    it('is not there until the mouse is over an entity', async () => {
      await recognized()

      expect(dialog()).toBeNull()
    })

    it('appears when the mouse is over an entity, and goes when it leaves', async () => {
      await recognized()

      await hover('Adam Mickiewicz')
      expect(dialog()).toBeInTheDocument()

      await userEvent.unhover(mark('Adam Mickiewicz'))
      expect(dialog()).toBeNull()
    })

    const ALL = ['persName', 'placeName', 'orgName', 'geogName', 'date', 'time']

    it('offers all the labels and "Not NER", and a Confirm button', async () => {
      await recognized()

      await hover('Paryżu')

      const box = within(dialog()!)
      expect(
        box.getAllByRole('radio').map((r) => (r as HTMLInputElement).value),
      ).toEqual([...ALL, 'Not NER'])
      expect(box.getByRole('button', { name: 'Confirm' })).toBeEnabled()
      expect(dialog()).toHaveAccessibleName('Named entity: Paryżu')
    })

    it('has the label that was recognized selected, and nothing else', async () => {
      await recognized()

      await hover('Paryżu')

      const box = within(dialog()!)
      expect(box.getByRole('radio', { name: 'placeName' })).toBeChecked()
      expect(box.getAllByRole('radio').filter((r) => (r as HTMLInputElement).checked)).toHaveLength(1)
    })

    it('offers the same labels for every entity, with its own one selected', async () => {
      await recognized()

      for (const [text, label] of [
        ['Adam Mickiewicz', 'persName'],
        ['Paryżu', 'placeName'],
        ['1840 roku', 'date'],
      ]) {
        await hover(text)
        const radios = within(dialog()!).getAllByRole('radio') as HTMLInputElement[]
        expect(radios.map((r) => r.value)).toEqual([...ALL, 'Not NER'])
        expect(radios.find((r) => r.checked)?.value).toBe(label)
        await userEvent.unhover(mark(text))
      }
    })

    it('also offers a label that is not in the list, should the model give one', async () => {
      await recognized({
        ner: () => [entity('Paryżu', TEXT.indexOf('Paryżu'), 'newLabel')],
      })

      await hover('Paryżu')

      const radios = within(dialog()!).getAllByRole('radio') as HTMLInputElement[]
      expect(radios.map((r) => r.value)).toEqual(['newLabel', ...ALL, 'Not NER'])
      expect(radios.find((r) => r.checked)?.value).toBe('newLabel')
    })

    it('shows the label of each entity: persName, placeName, date', async () => {
      await recognized()

      for (const [text, label] of [
        ['Adam Mickiewicz', 'persName'],
        ['Paryżu', 'placeName'],
        ['1840 roku', 'date'],
      ]) {
        await hover(text)
        expect(within(dialog()!).getByRole('radio', { name: label })).toBeChecked()
        await userEvent.unhover(mark(text))
      }
    })

    it('stays open while the mouse moves from the entity onto the dialog', async () => {
      await recognized()
      await hover('Paryżu')
      const entityMark = mark('Paryżu')
      const box = dialog()!

      // what a browser does when the pointer moves from the entity to the
      // dialog: out of the entity (towards the dialog), then over the dialog
      fireEvent.mouseOut(entityMark, { relatedTarget: box })
      fireEvent.mouseOver(box, { relatedTarget: entityMark })
      expect(dialog()).toBeInTheDocument()

      // ...and moving on, out of the dialog and into the text, closes it
      fireEvent.mouseOut(box, { relatedTarget: pageText() })
      expect(dialog()).toBeNull()
    })

    it('has the dialog touching the entity, with nothing between them', async () => {
      await recognized()
      await hover('Paryżu')

      // inside the same element as the entity, right after it
      expect(mark('Paryżu').nextElementSibling).toBe(dialog())
      expect(mark('Paryżu').parentElement).toContainElement(dialog())
    })

    it('does not change the text of the page', async () => {
      await recognized()
      await hover('Paryżu')
      await userEvent.unhover(mark('Paryżu'))

      expect(pageText().textContent).toBe(TEXT)
    })

    it('opens with the keyboard focus, and Escape closes it', async () => {
      await recognized()

      mark('Paryżu').focus()
      expect(await screen.findByRole('dialog')).toBeInTheDocument()
      await userEvent.keyboard('{Escape}')

      expect(dialog()).toBeNull()
    })

    it('shows only one dialog at a time', async () => {
      await recognized()
      await hover('Adam Mickiewicz')

      await userEvent.unhover(mark('Adam Mickiewicz'))
      await hover('Paryżu')

      expect(screen.getAllByRole('dialog')).toHaveLength(1)
      expect(dialog()).toHaveAccessibleName('Named entity: Paryżu')
    })
  })

  describe('Confirm with the label', () => {
    it('asks the backend to mark the entity in the XML, with the label', async () => {
      await recognized()
      await hover('Adam Mickiewicz')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(tagCalls()).toHaveLength(1))
      expect(tagCalls()[0].method).toBe('POST')
      expect(tagCalls()[0].url).toBe(`/editions/${UUID}/pages/0/entities`)
      expect(tagCalls()[0].body).toEqual({
        start: 0,
        end: 15,
        text: 'Adam Mickiewicz',
        label: 'persName',
      })
    })

    it('sends the label that the entity has: persName, placeName, date', async () => {
      await recognized()

      for (const [text, label] of [
        ['Adam Mickiewicz', 'persName'],
        ['Paryżu', 'placeName'],
        ['1840 roku', 'date'],
      ]) {
        await hover(text)
        await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
        await waitFor(() => expect(mark(text)).toHaveClass('confirmed'))
        expect(tagCalls().at(-1)!.body).toMatchObject({ text, label })
      }
      expect(tagCalls()).toHaveLength(3)
    })

    it('marks the entity in green and takes away its dialog', async () => {
      await recognized()
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      expect(dialog()).toBeNull()
      const [r, g, b] = getComputedStyle(mark('Paryżu')).color.match(/\d+/g)!.map(Number)
      expect(g).toBeGreaterThan(r) // green, not red
      expect(g).toBeGreaterThan(b)
      await userEvent.unhover(mark('Paryżu'))
      await hover('Paryżu')
      expect(dialog()).toBeNull() // nothing more to confirm
    })

    it('keeps the text and the other entities as they are', async () => {
      await recognized()
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      expect(pageText().textContent).toBe(TEXT)
      expect(mark('Adam Mickiewicz')).not.toHaveClass('confirmed')
      expect(mark('1840 roku')).not.toHaveClass('confirmed')
    })

    it('asks the backend about the text again, which is the same', async () => {
      await recognized()
      await hover('Paryżu')
      calls.length = 0

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
        `POST /editions/${UUID}/pages/0/entities`,
        'POST /tei/text', // the backend extracts the text of the new XML
        'POST /spellcheck', // ... and checks it
        'POST /tei/entities', // ... and reads the entities marked in it
      ])
    })

    it('is disabled while the request runs', async () => {
      await recognized()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (
            url: string,
            init?: RequestInit,
          ) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/entities')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )
      await hover('Adam Mickiewicz')
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      // while waiting: the other entities cannot be confirmed, nor pages changed
      await hover('Paryżu')
      expect(within(dialog()!).getByRole('button', { name: 'Confirm' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
      finish()
      await waitFor(() => expect(mark('Adam Mickiewicz')).toHaveClass('confirmed'))
    })

    it('shows the backend error, and the entity can still be confirmed', async () => {
      await recognized({
        tag: () =>
          new Error('The text at 27-33 is not "Paryżu": the page has changed'),
      })
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('the page has changed')
      expect(mark('Paryżu')).not.toHaveClass('confirmed')
      expect(pageText().textContent).toBe(TEXT)
      await userEvent.unhover(mark('Paryżu'))
      await hover('Paryżu')
      expect(dialog()).toBeInTheDocument()
    })
  })

  describe('correcting a wrong label', () => {
    async function confirmWith(text: string, label: string) {
      await hover(text)
      await userEvent.click(within(dialog()!).getByRole('radio', { name: label }))
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
    }

    it('marks the entity with the label that was chosen, not the recognized one', async () => {
      await recognized() // "Paryżu" was recognized as placeName

      await confirmWith('Paryżu', 'orgName')

      await waitFor(() => expect(tagCalls()).toHaveLength(1))
      expect(tagCalls()[0].body).toEqual({
        start: TEXT.indexOf('Paryżu'),
        end: TEXT.indexOf('Paryżu') + 6,
        text: 'Paryżu',
        label: 'orgName',
      })
    })

    it.each(['persName', 'placeName', 'orgName', 'geogName', 'date', 'time'])(
      'can choose %s',
      async (label) => {
        await recognized()

        await confirmWith('Paryżu', label)

        await waitFor(() => expect(tagCalls()).toHaveLength(1))
        expect(tagCalls()[0].body).toMatchObject({ text: 'Paryżu', label })
      },
    )

    it('sends the recognized label when it is left as it is', async () => {
      await recognized()
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(tagCalls()).toHaveLength(1))
      expect(tagCalls()[0].body).toMatchObject({ label: 'placeName' })
    })

    it('shows the entity as confirmed whichever label was chosen', async () => {
      await recognized()

      await confirmWith('Paryżu', 'persName')

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      expect(dialog()).toBeNull()
      expect(mark('Paryżu')).toHaveAttribute('title', 'persName, marked in the XML')
    })

    it('keeps the choice made in the dialog while it is open', async () => {
      await recognized()
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('radio', { name: 'date' }))

      expect(within(dialog()!).getByRole('radio', { name: 'date' })).toBeChecked()
      expect(within(dialog()!).getByRole('radio', { name: 'placeName' })).not.toBeChecked()
    })

    it('does not change the other entities', async () => {
      await recognized()

      await confirmWith('Paryżu', 'orgName')

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      await hover('Adam Mickiewicz')
      expect(
        (within(dialog()!).getAllByRole('radio') as HTMLInputElement[]).find((r) => r.checked)?.value,
      ).toBe('persName') // still as recognized
    })

    it('the backend says no to a label it does not know: the error is shown', async () => {
      await recognized({
        tag: (body) => new Error(`${body.label} is not an entity label`),
      })

      await confirmWith('Paryżu', 'time')

      expect(await screen.findByRole('alert')).toHaveTextContent('time is not an entity label')
      expect(mark('Paryżu')).not.toHaveClass('confirmed')
    })
  })

  describe('Not NER', () => {
    async function reject(text: string) {
      await hover(text)
      await userEvent.click(within(dialog()!).getByRole('radio', { name: 'Not NER' }))
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
    }

    it('takes the highlight away', async () => {
      await recognized()

      await reject('Paryżu')

      expect(
        Array.from(pageText().querySelectorAll('mark')).map((m) => m.textContent),
      ).toEqual(['Adam Mickiewicz', '1840 roku'])
      expect(pageText().textContent).toBe(TEXT)
      expect(dialog()).toBeNull()
    })

    it('changes nothing in the XML: no request is made', async () => {
      await recognized()
      calls.length = 0

      await reject('Paryżu')

      expect(calls).toEqual([])
    })

    it('leaves out the entity that was rejected', async () => {
      await recognized()
      expect(pageText().querySelectorAll('mark')).toHaveLength(3)

      await reject('Paryżu')

      expect(pageText().querySelectorAll('mark')).toHaveLength(2)
    })

    it('says there are none when all were rejected', async () => {
      await recognized()

      await reject('Paryżu')
      await reject('Adam Mickiewicz')
      await reject('1840 roku')

      expect(pageText().querySelectorAll('mark')).toHaveLength(0)
    })

    it('does not confirm anything if the choice is changed back to the label', async () => {
      await recognized()
      await hover('Paryżu')
      await userEvent.click(within(dialog()!).getByRole('radio', { name: 'Not NER' }))
      await userEvent.click(within(dialog()!).getByRole('radio', { name: 'placeName' }))

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(tagCalls()).toHaveLength(1))
      expect(tagCalls()[0].body).toMatchObject({ label: 'placeName' })
    })
  })

  describe('what is remembered', () => {
    const nerButton = () =>
      screen.getByRole('button', { name: 'Named entity recognition' })

    it('keeps the decisions when the entities are recognized again', async () => {
      await recognized()
      await hover('Paryżu')
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      await hover('Adam Mickiewicz')
      await userEvent.click(within(dialog()!).getByRole('radio', { name: 'Not NER' }))
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await userEvent.click(nerButton())
      await waitFor(() => expect(calls.filter((c) => c.url === '/ner')).toHaveLength(2))

      expect(mark('Paryżu')).toHaveClass('confirmed')
      expect(screen.queryByText('Adam Mickiewicz', { selector: 'mark' })).toBeNull()
      expect(mark('1840 roku')).not.toHaveClass('confirmed')
    })

    it('belong to their text: not shown for another page, back when it returns', async () => {
      await recognized({
        page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny tekst') }),
      })
      await hover('Paryżu')
      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
      await waitFor(() => expect(pageText().textContent).toBe('Inny tekst'))
      expect(pageText().querySelectorAll('mark')).toHaveLength(0)
      expect(screen.queryByText(/entit/)).toBeNull()

      await userEvent.click(screen.getByRole('button', { name: 'Previous page' }))
      await waitFor(() => expect(pageText().textContent).toBe(TEXT))
      // the page opens in the correction view: no entities in it
      expect(pageText().querySelectorAll('mark')).toHaveLength(0)
      calls.length = 0
      await userEvent.click(
        screen.getByRole('button', { name: 'Named entity recognition' }),
      )
      await waitFor(() => expect(pageText().querySelectorAll('mark')).toHaveLength(3))
      // what was found, and decided, for this text is back
      expect(mark('Paryżu')).toHaveClass('confirmed')
      expect(mark('Adam Mickiewicz')).not.toHaveClass('confirmed')
    })
  })
})

describe('structure view', () => {
  const TEXT = '84 KATEDRA\n\ncle przekazał\nI. Alfa\nII. Beta\nkoniec\n\nstopka 85'
  const options = (extra: Options = {}): Options => ({
    page: () => ({ content: xmlOf(TEXT) }),
    ...extra,
  })
  const structureButton = () => screen.getByRole('button', { name: 'Structure' })
  const dialog = () => screen.queryByRole('dialog', { name: 'Mark the selected text' })
  const dropdown = () => screen.getByRole('combobox', { name: 'Section' })
  const confirmButton = () => screen.getByRole('button', { name: 'Confirm' })
  const sectionCalls = () =>
    calls.filter((c) => /\/pages\/\d+\/sections$/.test(c.url))

  async function open(extra: Options = {}, shown = TEXT) {
    await openPage(options(extra))
    await waitFor(() => expect(pageText().textContent).toBe(shown))
  }

  async function openStructure(extra: Options = {}) {
    await open(extra)
    await userEvent.click(structureButton())
  }

  /** Select `selected` in the text, as the user does with the mouse. */
  function select(selected: string, occurrence = 0) {
    const box = pageText()
    const node = box.firstChild as Text
    let from = -1
    for (let i = 0; i <= occurrence; i++) from = node.data.indexOf(selected, from + 1)
    const range = document.createRange()
    range.setStart(node, from)
    range.setEnd(node, from + selected.length)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    fireEvent.mouseUp(box)
  }

  afterEach(() => window.getSelection()?.removeAllRanges())

  describe('the sections in the text', () => {
    const at = (selected: string, occurrence = 0) => {
      let from = -1
      for (let i = 0; i <= occurrence; i++) from = TEXT.indexOf(selected, from + 1)
      return from
    }
    const span = (selected: string, open: string, close: string, occurrence = 0) => ({
      start: at(selected, occurrence),
      end: at(selected, occurrence) + [...selected].length,
      open,
      close,
    })
    const HEADER = span('84 KATEDRA', '<fw type="header">', '</fw>')
    const FOOTER = span('stopka 85', '<fw type="footer">', '</fw>')
    const tags = (element: Element) => [
      element.getAttribute('data-open'),
      element.getAttribute('data-close'),
    ]
    const sections = () => [...pageText().querySelectorAll('.section')]

    it('are shown with their tags around them', async () => {
      await openStructure({ sections: () => [HEADER, FOOTER] })

      await waitFor(() => expect(sections()).toHaveLength(2))
      expect(sections().map((s) => s.textContent)).toEqual(['84 KATEDRA', 'stopka 85'])
      expect(sections().map(tags)).toEqual([
        ['<fw type="header">', '</fw>'],
        ['<fw type="footer">', '</fw>'],
      ])
    })

    it('are not text: the page text is the same, so selecting is unaffected', async () => {
      await openStructure({ sections: () => [HEADER, FOOTER] })
      await waitFor(() => expect(sections()).toHaveLength(2))

      expect(pageText().textContent).toBe(TEXT)
    })

    it('are not part of the text: the tags cannot be selected or edited', async () => {
      await openStructure({ sections: () => [HEADER] })
      await waitFor(() => expect(sections()).toHaveLength(1))

      // the tags are generated by the stylesheet, not elements or text nodes
      expect(pageText().textContent).not.toContain('<fw')
      expect(pageText().querySelectorAll('input, textarea, [contenteditable]')).toHaveLength(0)
    })

    it('can be nested, as a note group holds its notes', async () => {
      const NOTES = '1 jeden 2 dwa'
      const text = `Ala ${NOTES} b`
      const outer = { start: 4, end: 4 + NOTES.length, open: '<noteGrp>', close: '</noteGrp>' }
      const first = { start: 4, end: 11, open: '<note n="1">', close: '</note>' }
      const second = { start: 12, end: 17, open: '<note n="2">', close: '</note>' }
      await open(
        { page: () => ({ content: xmlOf(text) }), sections: () => [outer, first, second] },
        text,
      )
      await userEvent.click(structureButton())

      await waitFor(() => expect(sections()).toHaveLength(3))
      const group = sections()[0]
      expect(tags(group)).toEqual(['<noteGrp>', '</noteGrp>'])
      expect(group.textContent).toBe(NOTES)
      expect([...group.querySelectorAll('.section')].map(tags)).toEqual([
        ['<note n="1">', '</note>'],
        ['<note n="2">', '</note>'],
      ])
      expect(pageText().textContent).toBe(text)
    })

    it('can have the same place, the outer one first', async () => {
      const one = { ...span('84 KATEDRA', '<fw type="header">', '</fw>') }
      const inner = { ...one, open: '<fw type="pageNum">', close: '</fw>' }
      await openStructure({ sections: () => [one, inner] })

      await waitFor(() => expect(sections()).toHaveLength(2))
      expect(tags(sections()[0])).toEqual(['<fw type="header">', '</fw>'])
      expect(tags(sections()[0].querySelector('.section')!)).toEqual([
        '<fw type="pageNum">',
        '</fw>',
      ])
    })

    it('spans lines and paragraphs, as a table of contents does', async () => {
      const toc = span('I. Alfa\nII. Beta\nkoniec', '<div type="toc">', '</div>')
      await openStructure({ sections: () => [toc] })

      await waitFor(() => expect(sections()).toHaveLength(1))
      expect(sections()[0].textContent).toBe('I. Alfa\nII. Beta\nkoniec')
    })

    it('are placed by code points: after an emoji too', async () => {
      const text = '😀 a 84 b'
      const number = { start: 4, end: 6, open: '<fw type="pageNum">', close: '</fw>' }
      await open(
        { page: () => ({ content: xmlOf(text) }), sections: () => [number] },
        text,
      )
      await userEvent.click(structureButton())

      await waitFor(() => expect(sections()).toHaveLength(1))
      expect(sections()[0].textContent).toBe('84')
    })

    it('are asked for the XML of the page', async () => {
      await openStructure({ sections: () => [] })

      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/tei/sections')).toHaveLength(1),
      )
      expect(calls.find((c) => c.url === '/tei/sections')!.body).toEqual({
        xml: xmlOf(TEXT),
      })
    })

    it('are shown again, from the new XML, after marking a section', async () => {
      const marked = xmlOf(`${TEXT} `)
      await openStructure({
        section: () => ({ content: marked }),
        sections: (xml) => (xml === marked ? [HEADER] : []),
      })
      expect(sections()).toHaveLength(0)

      select('84 KATEDRA')
      await userEvent.selectOptions(dropdown(), 'Page header')
      await userEvent.click(confirmButton())

      await waitFor(() => expect(sections()).toHaveLength(1))
      expect(tags(sections()[0])).toEqual(['<fw type="header">', '</fw>'])
    })

    it('are not shown for another page than their own', async () => {
      await openStructure({
        page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny tekst') }),
        sections: (xml) => (xml === xmlOf(TEXT) ? [HEADER] : []),
      })
      await waitFor(() => expect(sections()).toHaveLength(1))

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(pageText().textContent).toBe('Inny tekst'))
      expect(sections()).toHaveLength(0)
    })

    it('are not shown in the text view', async () => {
      await open({ sections: () => [HEADER] })

      expect(sections()).toHaveLength(0)
    })

    it('show just the text if the backend cannot say, and the page still works', async () => {
      await openStructure({ sections: () => new Error('no') })

      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/tei/sections')).toHaveLength(1),
      )
      expect(sections()).toHaveLength(0)
      expect(pageText().textContent).toBe(TEXT)
      select('84 KATEDRA')
      expect(dialog()).toBeInTheDocument()
    })

    it('leave a selection across them as it is: offsets are those of the text', async () => {
      await openStructure({ sections: () => [HEADER] })
      await waitFor(() => expect(sections()).toHaveLength(1))
      const box = pageText()
      const range = document.createRange()
      // from inside the header to the next line, over the element's border
      range.setStart(sections()[0].firstChild!, 3)
      range.setEnd(box.lastChild!, 6)
      window.getSelection()!.removeAllRanges()
      window.getSelection()!.addRange(range)
      fireEvent.mouseUp(box)

      await userEvent.selectOptions(dropdown(), 'Table of contents')
      await userEvent.click(confirmButton())

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      const start = TEXT.indexOf('84 KATEDRA') + 3
      const end = TEXT.indexOf('84 KATEDRA') + 10 + 6 // 6 characters after the header
      const selected = TEXT.slice(start, end).replace(/\s+$/, '')
      expect(sectionCalls()[0].body).toMatchObject({
        start,
        end: start + selected.length,
        text: selected,
      })
    })
  })

  describe('where the dialog appears', () => {
    // jsdom has no layout: the box is 400px high from y=100, and the selection
    // is where each test says.
    const AREA = { left: 0, top: 100, right: 300, bottom: 500, width: 300, height: 400 }
    const rectOf = (top: number, bottom: number) => ({
      left: 20, right: 120, top, bottom, width: 100, height: bottom - top,
    })
    let selected = rectOf(0, 0)
    const original = Element.prototype.getBoundingClientRect
    const originalRange = Range.prototype.getBoundingClientRect

    beforeEach(() => {
      Element.prototype.getBoundingClientRect = function (this: Element) {
        return (
          this.classList.contains('structure-wrap') ? AREA : original.call(this)
        ) as DOMRect
      }
      Range.prototype.getBoundingClientRect = () => selected as DOMRect
    })
    afterEach(() => {
      Element.prototype.getBoundingClientRect = original
      Range.prototype.getBoundingClientRect = originalRange
    })

    it('is below a selection in the upper half of the box', async () => {
      await openStructure()
      selected = rectOf(120, 140) // 20-40px into the box

      select('84 KATEDRA')

      const style = dialog()!.style
      expect(style.top).toBe('46px') // 140 - 100 + 6
      expect(style.bottom).toBe('')
    })

    it('is above a selection in the lower half, so that it stays in sight', async () => {
      await openStructure()
      selected = rectOf(440, 460) // the last line, 340-360px into the box

      select('stopka 85')

      const style = dialog()!.style
      expect(style.bottom).toBe('66px') // 500 - 440 + 6
      expect(style.top).toBe('')
      expect(dropdown()).toBeInTheDocument() // still usable there
    })

    it('goes by where the selection ends: a long one ending low is above', async () => {
      await openStructure()
      selected = rectOf(130, 330) // ends at 230px of 400: just past the middle

      select('cle przekazał\nI. Alfa\nII. Beta\nkoniec')

      expect(dialog()!.style.bottom).toBe('376px') // 500 - 130 + 6
    })

    it('is below a selection that ends exactly in the middle', async () => {
      await openStructure()
      selected = rectOf(280, 300) // ends at 200px: the middle

      select('I. Alfa')

      expect(dialog()!.style.top).toBe('206px')
    })

    it('can be used from above: choosing and confirming works', async () => {
      await openStructure()
      selected = rectOf(440, 460)
      select('stopka 85')

      await userEvent.selectOptions(dropdown(), 'Page footer')
      await userEvent.click(confirmButton())

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
    })

    it('is placed again for each selection', async () => {
      await openStructure()
      selected = rectOf(440, 460)
      select('stopka 85')
      expect(dialog()!.style.bottom).not.toBe('')

      selected = rectOf(120, 140)
      select('84 KATEDRA')

      expect(dialog()!.style.top).toBe('46px')
      expect(dialog()!.style.bottom).toBe('')
    })
  })

  describe('the view', () => {
    it('is switched to with the Structure button, which shows it is on', async () => {
      await open()
      expect(structureButton()).toHaveAttribute('aria-pressed', 'false')

      await userEvent.click(structureButton())

      expect(structureButton()).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByText(/Select some text to mark it/)).toBeInTheDocument()
    })

    it('shows the text of the page, and nothing else in it', async () => {
      await openStructure()

      expect(pageText().textContent).toBe(TEXT)
      expect(pageText()).toBeInTheDocument()
    })

    it('goes back to the correction view with the Correction button', async () => {
      await openStructure()

      await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

      expect(structureButton()).toHaveAttribute('aria-pressed', 'false')
      expect(screen.getByRole('button', { name: 'Correction' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
      expect(screen.queryByText(/Select some text to mark it/)).toBeNull()
    })

    it('is left with the NER button too, as the entities are shown in the text', async () => {
      await open({ ner: () => [] })
      await userEvent.click(structureButton())

      await userEvent.click(
        screen.getByRole('button', { name: 'Named entity recognition' }),
      )

      expect(structureButton()).toHaveAttribute('aria-pressed', 'false')
    })

    it('is left for the correction view when moving to another page', async () => {
      await open({ page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny tekst') }) })
      await userEvent.click(structureButton())

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(pageText().textContent).toBe('Inny tekst'))
      expect(structureButton()).toHaveAttribute('aria-pressed', 'false')
      expect(screen.getByRole('button', { name: 'Correction' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
    })

    it('only asks for the sections marked in the XML, which changes nothing', async () => {
      await open()
      const before = calls.length

      await userEvent.click(structureButton())

      await waitFor(() => expect(calls).toHaveLength(before + 1))
      expect(calls.slice(before).map((c) => `${c.method} ${c.url}`)).toEqual([
        'POST /tei/sections',
      ])
    })
  })

  describe('selecting text', () => {
    it('shows no dialog until text is selected', async () => {
      await openStructure()

      expect(dialog()).toBeNull()
    })

    it('shows a dialog when text is selected', async () => {
      await openStructure()

      select('84 KATEDRA')

      expect(dialog()).toBeInTheDocument()
    })

    it('the dialog has a dropdown of the sections and a Confirm button', async () => {
      await openStructure()

      select('84 KATEDRA')

      expect(
        within(dropdown()).getAllByRole('option').map((o) => o.textContent),
      ).toEqual([
        'Choose a section…',
        'Page header',
        'Page footer',
        'Page number',
        'Table of contents',
        'Note group',
        'Note',
      ])
      expect(confirmButton()).toBeInTheDocument()
    })

    it('nothing is chosen at first, so Confirm cannot be pressed', async () => {
      await openStructure()

      select('84 KATEDRA')

      expect(dropdown()).toHaveValue('')
      expect(confirmButton()).toBeDisabled()
    })

    it('Confirm can be pressed once a section is chosen', async () => {
      await openStructure()
      select('84 KATEDRA')

      await userEvent.selectOptions(dropdown(), 'Page header')

      expect(confirmButton()).toBeEnabled()
    })

    it('the dialog is not part of the text', async () => {
      await openStructure()

      select('84 KATEDRA')

      expect(pageText().textContent).toBe(TEXT)
      expect(pageText()).not.toContainElement(dialog())
    })

    it('selecting only sends nothing', async () => {
      await openStructure()
      const before = calls.length

      select('84 KATEDRA')

      expect(calls).toHaveLength(before)
    })

    it('a new selection replaces the dialog, with nothing chosen yet', async () => {
      await openStructure()
      select('84 KATEDRA')
      await userEvent.selectOptions(dropdown(), 'Page header')

      select('stopka 85')

      expect(screen.getAllByRole('dialog')).toHaveLength(1)
      expect(dropdown()).toHaveValue('')
    })

    it('the dialog goes when the selection is cleared by a click', async () => {
      await openStructure()
      select('84 KATEDRA')

      window.getSelection()!.removeAllRanges()
      fireEvent.mouseUp(pageText())

      expect(dialog()).toBeNull()
    })

    it('a selection of only spaces gives no dialog', async () => {
      await openStructure()

      select('\n\n')

      expect(dialog()).toBeNull()
    })

    it('a selection outside the text gives no dialog', async () => {
      await openStructure()
      const range = document.createRange()
      range.selectNodeContents(screen.getByText(/Select some text to mark it/))
      window.getSelection()!.removeAllRanges()
      window.getSelection()!.addRange(range)

      fireEvent.mouseUp(pageText())

      expect(dialog()).toBeNull()
    })

    it('Escape closes the dialog, wherever the keyboard focus is', async () => {
      await openStructure()
      select('84 KATEDRA')

      await userEvent.keyboard('{Escape}')

      expect(dialog()).toBeNull()
      expect(window.getSelection()!.isCollapsed).toBe(true)
    })

    it('Escape closes the dialog while the dropdown has the focus', async () => {
      await openStructure()
      select('84 KATEDRA')
      dropdown().focus()

      await userEvent.keyboard('{Escape}')

      expect(dialog()).toBeNull()
    })

    it('Escape does nothing when there is no dialog', async () => {
      await openStructure()

      await userEvent.keyboard('{Escape}')

      expect(dialog()).toBeNull()
      expect(pageText().textContent).toBe(TEXT)
    })

    it('a selection made with the keyboard (Shift and the arrows) shows it too', async () => {
      await openStructure()
      const box = pageText()
      const node = box.firstChild as Text
      const range = document.createRange()
      range.setStart(node, 0)
      range.setEnd(node, 10)
      window.getSelection()!.removeAllRanges()
      window.getSelection()!.addRange(range)

      fireEvent.keyUp(box, { key: 'ArrowRight', shiftKey: true })

      expect(dialog()).toBeInTheDocument()
    })

    it('the dialog is for the text it was made in: another page has none', async () => {
      await open({ page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny tekst') }) })
      await userEvent.click(structureButton())
      select('84 KATEDRA')

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(pageText().textContent).toBe('Inny tekst'))
      expect(dialog()).toBeNull()
    })
  })

  describe('confirming', () => {
    async function mark(selected: string, section: string, occurrence = 0) {
      select(selected, occurrence)
      await userEvent.selectOptions(dropdown(), section)
      await userEvent.click(confirmButton())
    }

    it.each([
      ['Page header', 'header'],
      ['Page footer', 'footer'],
      ['Page number', 'page_number'],
      ['Table of contents', 'toc'],
      ['Note group', 'note_group'],
      ['Note', 'note'],
    ])('sends %s to the backend as %s', async (label, section) => {
      await openStructure()

      await mark('84 KATEDRA', label)

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].method).toBe('POST')
      expect(sectionCalls()[0].url).toBe(`/editions/${UUID}/pages/0/sections`)
      expect(sectionCalls()[0].body).toEqual({
        start: 0,
        end: 10,
        text: '84 KATEDRA',
        section,
      })
    })

    describe('the number of a note', () => {
      const noteNumber = () => screen.queryByRole('textbox', { name: 'Note number' })

      it('is asked for only when the section is a note', async () => {
        await openStructure()
        select('I. Alfa')
        expect(noteNumber()).toBeNull()

        await userEvent.selectOptions(dropdown(), 'Note')
        expect(noteNumber()).toBeInTheDocument()

        await userEvent.selectOptions(dropdown(), 'Note group')
        expect(noteNumber()).toBeNull()
      })

      it('is sent as n with the note', async () => {
        await openStructure()
        select('I. Alfa')
        await userEvent.selectOptions(dropdown(), 'Note')
        await userEvent.type(noteNumber()!, ' 1 ')
        await userEvent.click(confirmButton())

        await waitFor(() => expect(sectionCalls()).toHaveLength(1))
        expect(sectionCalls()[0].body).toEqual({
          start: TEXT.indexOf('I. Alfa'),
          end: TEXT.indexOf('I. Alfa') + 7,
          text: 'I. Alfa',
          section: 'note',
          n: '1',
        })
      })

      it('is left out when it is empty: the note has no n', async () => {
        await openStructure()
        select('I. Alfa')
        await userEvent.selectOptions(dropdown(), 'Note')
        await userEvent.type(noteNumber()!, '   ')
        await userEvent.click(confirmButton())

        await waitFor(() => expect(sectionCalls()).toHaveLength(1))
        expect(sectionCalls()[0].body).not.toHaveProperty('n')
      })

      it('is not sent when the section is changed to another after typing it', async () => {
        await openStructure()
        select('I. Alfa')
        await userEvent.selectOptions(dropdown(), 'Note')
        await userEvent.type(noteNumber()!, '7')
        await userEvent.selectOptions(dropdown(), 'Note group')
        await userEvent.click(confirmButton())

        await waitFor(() => expect(sectionCalls()).toHaveLength(1))
        expect(sectionCalls()[0].body).not.toHaveProperty('n')
      })

      it('confirms with Enter', async () => {
        await openStructure()
        select('I. Alfa')
        await userEvent.selectOptions(dropdown(), 'Note')
        await userEvent.type(noteNumber()!, '3{Enter}')

        await waitFor(() => expect(sectionCalls()).toHaveLength(1))
        expect(sectionCalls()[0].body).toMatchObject({ section: 'note', n: '3' })
      })

      it('is empty again for the next selection', async () => {
        await openStructure()
        select('I. Alfa')
        await userEvent.selectOptions(dropdown(), 'Note')
        await userEvent.type(noteNumber()!, '3')
        select('II. Beta')

        await userEvent.selectOptions(dropdown(), 'Note')

        expect(noteNumber()).toHaveValue('')
      })
    })

    it('sends the offsets of the selection, over line breaks too', async () => {
      await openStructure()

      await mark('I. Alfa\nII. Beta', 'Table of contents')

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      const start = TEXT.indexOf('I. Alfa')
      expect(sectionCalls()[0].body).toEqual({
        start,
        end: start + 'I. Alfa\nII. Beta'.length,
        text: 'I. Alfa\nII. Beta',
        section: 'toc',
      })
    })

    it('sends the offsets of the occurrence that was selected', async () => {
      await open({ page: () => ({ content: xmlOf('84 a 84 b') }) }, '84 a 84 b')
      await userEvent.click(structureButton())

      select('84', 1)
      await userEvent.selectOptions(dropdown(), 'Page number')
      await userEvent.click(confirmButton())

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].body).toMatchObject({ start: 5, end: 7, text: '84' })
    })

    it('sends the text without the spaces and line breaks around the selection', async () => {
      await openStructure()

      await mark('\nI. Alfa\n', 'Table of contents')

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].body).toMatchObject({ text: 'I. Alfa' })
    })

    it('counts code points, as the backend does, when there is an emoji before it', async () => {
      const emoji = '😀 84 😀'
      await open({ page: () => ({ content: xmlOf(emoji) }) }, emoji)
      await userEvent.click(structureButton())

      select('84')
      await userEvent.selectOptions(dropdown(), 'Page number')
      await userEvent.click(confirmButton())

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].body).toMatchObject({ start: 2, end: 4, text: '84' })
    })

    it('closes the dialog and clears the selection', async () => {
      await openStructure()

      await mark('84 KATEDRA', 'Page header')

      await waitFor(() => expect(dialog()).toBeNull())
      expect(window.getSelection()!.isCollapsed).toBe(true)
    })

    it('tells what was marked', async () => {
      await openStructure()

      await mark('84 KATEDRA', 'Page footer')

      expect(await screen.findByRole('status')).toHaveTextContent('Marked as page footer.')
    })

    it('asks the backend for the text again, which can have changed', async () => {
      // a table of contents splits a paragraph: the text has a blank line more
      await openStructure({
        section: () => ({
          content: xmlOf(TEXT.replace('I. Alfa\nII. Beta', '\nI. Alfa\nII. Beta\n')),
        }),
      })
      calls.length = 0

      await mark('I. Alfa\nII. Beta', 'Table of contents')

      await waitFor(() =>
        expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
          `POST /editions/${UUID}/pages/0/sections`,
          'POST /tei/text',
          'POST /spellcheck', // the words of the new text
          'POST /tei/sections', // and what is marked in the new XML
        ]),
      )
    })

    it('stays in the structure view with the new text', async () => {
      await openStructure({
        section: () => ({ content: xmlOf(`${TEXT}!`) }),
      })

      await mark('84 KATEDRA', 'Page header')

      await waitFor(() => expect(pageText().textContent).toBe(`${TEXT}!`))
      expect(structureButton()).toHaveAttribute('aria-pressed', 'true')
    })

    it('can mark another section right after', async () => {
      await openStructure()
      await mark('84 KATEDRA', 'Page header')
      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      await waitFor(() => expect(confirmButton).toThrow()) // the dialog is gone

      await mark('stopka 85', 'Page footer')

      await waitFor(() => expect(sectionCalls()).toHaveLength(2))
      expect(sectionCalls()[1].body).toMatchObject({ text: 'stopka 85', section: 'footer' })
    })

    it('is disabled while the request runs', async () => {
      await openStructure()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (
            url: string,
            init?: RequestInit,
          ) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/sections')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )

      await mark('84 KATEDRA', 'Page header')

      await waitFor(() => expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled())
      select('stopka 85')
      await userEvent.selectOptions(dropdown(), 'Page footer')
      expect(confirmButton()).toBeDisabled()
      finish()
      await waitFor(() => expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled())
    })

    it('shows the backend reason when the section cannot be marked, and the text as it was', async () => {
      await openStructure({
        section: () => new Error('The selection spans more than one paragraph or line'),
      })

      await mark('84 KATEDRA', 'Page header')

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The selection spans more than one paragraph or line',
      )
      expect(pageText().textContent).toBe(TEXT)
      expect(screen.queryByRole('status')).toBeNull() // nothing was marked
    })

    it('the notice goes with the next request', async () => {
      await openStructure()
      await mark('84 KATEDRA', 'Page header')
      expect(await screen.findByRole('status')).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    })
  })
})

/** The words of a text as the backend reports them, with the given ones wrong. */
function wordsOf(text: string, wrong: string[] = []): Word[] {
  const words: Word[] = []
  for (const match of text.matchAll(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu)) {
    const start = [...text.slice(0, match.index)].length
    const length = [...match[0]].length
    words.push({
      text: match[0],
      start,
      end: start + length,
      correct: !wrong.includes(match[0]),
    })
  }
  return words
}

describe('spell check', () => {
  const TEXT = 'Ala ma kota, a jey tey nie ma.'
  const WRONG = ['jey', 'tey']
  const spellOptions = (): Options => ({
    page: () => ({ content: xmlOf(TEXT) }),
    spell: (text) => wordsOf(text, WRONG),
  })
  const spellCalls = () => calls.filter((c) => c.url === '/spellcheck')
  const wordSpans = () => Array.from(pageText().querySelectorAll('.word'))
  const misspelled = () =>
    Array.from(pageText().querySelectorAll('.word.misspelled')).map((w) => w.textContent)

  async function open(options: Options = spellOptions()) {
    await openPage(options)
    await waitFor(() => expect(pageText().textContent).toBe(options.page ? TEXT : TEXT))
    await waitFor(() => expect(wordSpans().length).toBeGreaterThan(0))
  }

  it('checks the text as soon as the page is shown, without a click', async () => {
    await openPage(spellOptions())

    await waitFor(() => expect(spellCalls()).toHaveLength(1))
    expect(calledUrls()).toEqual([
      `GET /editions/${UUID}/pages/0`,
      'POST /tei/text',
      'POST /spellcheck',
    ])
  })

  it('sends the text, not the XML', async () => {
    await open()

    expect(spellCalls()[0].body).toEqual({ text: TEXT, edition_id: UUID })
  })

  it('shows the misspelled words in red and the others as they are', async () => {
    await open()

    expect(misspelled()).toEqual(['jey', 'tey'])
    const [r, g, b] = getComputedStyle(
      pageText().querySelector('.word.misspelled')!,
    ).color.match(/\d+/g)!.map(Number)
    expect(r).toBeGreaterThan(150)
    expect(g).toBeLessThan(50)
    expect(b).toBeLessThan(50)
    const correct = pageText().querySelector('.word:not(.misspelled)')!
    expect(getComputedStyle(correct).color).not.toBe(
      getComputedStyle(pageText().querySelector('.word.misspelled')!).color,
    )
  })

  it('underlines the misspelled words', async () => {
    await open()

    const style = getComputedStyle(pageText().querySelector('.word.misspelled')!)
    expect(style.textDecoration).toContain('underline')
  })

  it('makes every word of the text a word: the correct ones too', async () => {
    await open()

    expect(wordSpans().map((w) => w.textContent)).toEqual([
      'Ala', 'ma', 'kota', 'a', 'jey', 'tey', 'nie', 'ma',
    ])
  })

  it('does not change the text', async () => {
    await open()

    expect(pageText().textContent).toBe(TEXT)
  })

  it('shows no count of the misspelled words', async () => {
    await open()

    expect(screen.queryByText(/\d+ misspelled/)).toBeNull()
    expect(screen.queryByText('No misspelled words')).toBeNull()
  })

  it('says when all words are correct', async () => {
    await open({ ...spellOptions(), spell: (text) => wordsOf(text, []) })

    expect(misspelled()).toEqual([])
  })

  it('shows no count when the spelling was not checked', async () => {
    await openPage({ ...spellOptions(), spell: () => new Error('The pl_PL dictionary is not available') })
    await screen.findByRole('alert')

    expect(screen.queryByText(/misspelled/)).toBeNull()
  })

  it('shows the backend reason when the spelling cannot be checked, and the text anyway', async () => {
    await openPage({
      ...spellOptions(),
      spell: () => new Error('The pl_PL dictionary is not available'),
    })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The spelling cannot be checked: The pl_PL dictionary is not available',
    )
    expect(pageText().textContent).toBe(TEXT)
    expect(wordSpans()).toHaveLength(0) // no words known: nothing to click
  })

  it('does not check the spelling when the text could not be extracted', async () => {
    await openPage({ ...spellOptions(), tei: () => new Error('The XML is empty') })
    await screen.findByRole('alert')

    expect(spellCalls()).toHaveLength(0)
  })

  it('checks the next page too, with its own text', async () => {
    await open({
      page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny teksz') }),
      spell: (text) => wordsOf(text, ['teksz']),
    })

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() => expect(misspelled()).toEqual(['teksz']))
    expect(spellCalls().map((c) => c.body)).toEqual([
      { text: TEXT, edition_id: UUID },
      { text: 'Inny teksz', edition_id: UUID },
    ])
  })

  it('uses code point offsets, as the backend does, after emoji', async () => {
    const emoji = '😀😀 jey ma 😀 tey'
    await openPage({
      page: () => ({ content: xmlOf(emoji) }),
      spell: (text) => wordsOf(text, ['jey', 'tey']),
    })

    await waitFor(() => expect(misspelled()).toEqual(['jey', 'tey']))
    expect(pageText().textContent).toBe(emoji)
  })

  it('shows a word that does not fit the text as plain text instead of guessing', async () => {
    await open({
      page: () => ({ content: xmlOf(TEXT) }),
      spell: () => [
        { text: 'jey', start: 15, end: 18, correct: false },
        { text: 'zzz', start: 4, end: 7, correct: false }, // the text there is "ma "
      ],
    })

    expect(misspelled()).toEqual(['jey'])
    expect(pageText().textContent).toBe(TEXT)
  })

  it('makes the misspelled words reachable with the keyboard, and only those', async () => {
    await open()

    const [first] = Array.from(pageText().querySelectorAll('.word.misspelled'))
    expect(first).toHaveAttribute('tabindex', '0')
    expect(pageText().querySelector('.word:not(.misspelled)')).not.toHaveAttribute(
      'tabindex',
    )
  })
})

describe('the buttons of the word editor', () => {
  const TEXT = 'Ala jey tey ma\nkota'
  const group = () => screen.getByRole('group', { name: 'Actions for "jey"' })

  async function open() {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      spell: (text) => wordsOf(text, ['jey', 'tey']),
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
    await userEvent.click(screen.getByText('jey', { selector: '.word' }))
  }

  it('are one group, with the buttons for the word', async () => {
    await open()

    const buttons = within(group()).getAllByRole('button')

    expect(buttons.map((b) => b.textContent)).toEqual([
      'Legal spelling',
      '← Join with previous',
      'Join with next →',
      '↵ New line here',
    ])
  })

  it('float over the text: the group is out of the line, under the field', async () => {
    await open()

    const editor = document.querySelector('.word-editor') as HTMLElement
    expect(getComputedStyle(editor).position).toBe('relative')
    expect(getComputedStyle(group()).position).toBe('absolute')
    expect(getComputedStyle(group()).top).not.toBe('auto')
    expect(getComputedStyle(group()).zIndex).not.toBe('auto')
    // the field is the only thing of the editor in the line
    expect(Array.from(editor.children).filter((c) => getComputedStyle(c).position !== 'absolute')).toEqual([
      screen.getByRole('textbox', { name: /^Correct / }),
    ])
  })

  it('leave the text of the line as it is', async () => {
    await open()

    // the words around it are in the same place as before: nothing was added to the line
    expect(pageText().textContent!.replace(group().textContent!, '')).toContain('Ala ')
    expect(group().closest('.word-editor')).toBe(document.querySelector('.word-editor'))
  })

  it('stay with the field while the user types, and work as before', async () => {
    await open()

    await userEvent.keyboard('jej')
    await userEvent.click(within(group()).getByRole('button', { name: 'Join with next →' }))

    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/joins'))).toHaveLength(1))
  })

  describe('near the edge of the box', () => {
    const original = Element.prototype.getBoundingClientRect
    let field = { left: 10, right: 50, top: 20, bottom: 40 }

    beforeEach(() => {
      field = { left: 10, right: 50, top: 20, bottom: 40 }
      Element.prototype.getBoundingClientRect = function (this: Element) {
        const rect = (r: Partial<DOMRect>) => ({ x: 0, y: 0, width: 0, height: 0, ...r }) as DOMRect
        if (this.classList.contains('page-box')) {
          return rect({ left: 0, right: 300, top: 0, bottom: 200 })
        }
        if (this.classList.contains('word-editor')) return rect(field)
        if (this.classList.contains('word-editor-actions')) {
          return rect({ width: 200, height: 60 })
        }
        return original.call(this)
      }
    })
    afterEach(() => {
      Element.prototype.getBoundingClientRect = original
    })

    it('are under the field, at its left, when there is room', async () => {
      await open()

      expect(group().className).not.toMatch(/right|above/)
    })

    it('line up with the right edge of the field when they would stick out on the right', async () => {
      field = { left: 250, right: 290, top: 20, bottom: 40 }

      await open()

      expect(group()).toHaveClass('right')
      expect(group()).not.toHaveClass('above')
    })

    it('go above the field when they would stick out at the bottom', async () => {
      field = { left: 10, right: 50, top: 150, bottom: 190 }

      await open()

      expect(group()).toHaveClass('above')
      expect(group()).not.toHaveClass('right')
    })

    it('stay where they are if the other side has no room either', async () => {
      field = { left: 10, right: 50, top: 20, bottom: 190 } // no room above, none below
      await open()
      expect(group()).not.toHaveClass('above')
    })
  })
})

describe('applying a correction to the entire edition', () => {
  const TEXT = 'Ala ma kota, a jćj tćj nie ma.'
  const similarCalls = () => calls.filter((c) => c.url.includes('/similar-misspellings'))
  const correctionCalls = () => calls.filter((c) => c.url.endsWith('/corrections'))
  const offer = () =>
    screen.queryByRole('dialog', { name: 'Apply the correction to the entire edition' })
  const review = () => screen.queryByRole('dialog', { name: 'The same mistake in the edition' })
  const applyButton = () => screen.getByRole('button', { name: 'Apply it to the entire edition' })

  const PLACES: PageMatches[] = [
    {
      page_number: 1,
      tokens: [
        { text: 'samćj', start: 4, end: 9, suggestion: 'saméj' },
        { text: 'tćj', start: 20, end: 23, suggestion: 'téj' },
      ],
    },
    { page_number: 3, tokens: [{ text: 'bardzićj', start: 7, end: 15, suggestion: 'bardziéj' }] },
  ]

  async function open(extra: Options = {}) {
    await openPage({
      page: (number) =>
        number === 1
          ? { content: xmlOf('To samćj i tćj tu') }
          : number === 3
            ? { content: xmlOf('To bardzićj tu') }
            : { content: xmlOf(TEXT) },
      spell: (text) => wordsOf(text, ['jćj', 'tćj']),
      similar: () => PLACES,
      ...extra,
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }
  const correct = async (from = 'jćj', to = 'jéj') => {
    await userEvent.click(screen.getByText(from, { selector: '.word' }))
    await userEvent.keyboard(`${to}{Enter}`)
  }
  /** Correct a word, accept the offer, and wait for the list. */
  async function openReview(extra: Options = {}) {
    await open(extra)
    await correct()
    await userEvent.click(await screen.findByRole('button', { name: 'Apply it to the entire edition' }))
    await screen.findByRole('dialog', { name: 'The same mistake in the edition' })
  }

  describe('the offer', () => {
    it('comes up when a word was corrected, with the two buttons', async () => {
      await open()

      await correct()

      expect(await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })).toBeInTheDocument()
      expect(within(offer()!).getByText('jćj')).toBeInTheDocument()
      expect(within(offer()!).getByText('jéj')).toBeInTheDocument()
      expect(applyButton()).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Nein Danke' })).toBeInTheDocument()
    })

    describe('a hyphen that continues a word on the next line', () => {
      const OCR = 'Ala ma abso\nlutely tu'
      const openOcr = () =>
        open({
          page: () => ({ content: xmlOf(OCR) }),
          spell: (text) => wordsOf(text, ['abso', 'lutely']),
        })

      it('is no reason for an offer: the last word of a line gets a "-"', async () => {
        await openOcr()

        await correct('abso', 'abso-')

        await waitFor(() => expect(correctionCalls()).toHaveLength(1))
        expect(correctionCalls()[0].body).toMatchObject({ before: 'abso', after: 'abso-' })
        await waitFor(() => expect(pageText().textContent).toBe('Ala ma abso-\nlutely tu'))
        expect(offer()).toBeNull()
        expect(similarCalls()).toHaveLength(0)
      })

      it('is not made a legal spelling either', async () => {
        await openOcr()

        await correct('abso', 'abso-')

        await waitFor(() => expect(pageText().textContent).toBe('Ala ma abso-\nlutely tu'))
        expect(calls.filter((c) => c.url.endsWith('/legal-spellings'))).toHaveLength(0)
      })

      it('is still a legal spelling when the hyphen is in the middle of a line', async () => {
        await openOcr()

        await correct('lutely', 'lutely-') // "lutely" is followed by " tu"

        await waitFor(() =>
          expect(calls.filter((c) => c.url.endsWith('/legal-spellings'))).toHaveLength(1),
        )
        expect(calls.filter((c) => c.url.endsWith('/legal-spellings'))[0].body).toEqual({
          spelling: 'lutely-',
        })
      })

      it('is made a legal spelling, as always, when the word changes with the hyphen', async () => {
        await openOcr()

        await correct('abso', 'abzo-')

        await waitFor(() =>
          expect(calls.filter((c) => c.url.endsWith('/legal-spellings'))).toHaveLength(1),
        )
        expect(calls.filter((c) => c.url.endsWith('/legal-spellings'))[0].body).toEqual({
          spelling: 'abzo-',
        })
      })

      it('is still corrected, as always', async () => {
        await openOcr()

        await correct('abso', 'abso-')

        await waitFor(() => expect(pageText().textContent).toContain('abso-'))
        expect(screen.queryByRole('alert')).toBeNull()
      })

      it('is an offer in the middle of a line: it is not the end of one', async () => {
        await openOcr()

        await correct('Ala', 'Ala-')

        expect(await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })).toBeInTheDocument()
      })

      it('is an offer for another change of the last word of a line', async () => {
        await openOcr()

        await correct('abso', 'absoo')

        expect(await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })).toBeInTheDocument()
      })

      it('is an offer when the hyphen comes with another change', async () => {
        await openOcr()

        await correct('abso', 'abzo-')

        expect(await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })).toBeInTheDocument()
      })

      it('is no reason for an offer for the last word of the page, which ends a line too', async () => {
        await open({
          page: () => ({ content: xmlOf('Ala ma abso') }),
          spell: (text) => wordsOf(text, ['abso']),
        })

        await correct('abso', 'abso-')

        await waitFor(() => expect(pageText().textContent).toBe('Ala ma abso-'))
        expect(offer()).toBeNull()
      })

      it('does not take away an offer that is open: it is just not made', async () => {
        await openOcr()
        await correct('Ala', 'Alę')
        await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })

        await correct('abso', 'abso-')

        await waitFor(() => expect(pageText().textContent).toContain('abso-'))
        expect(within(offer()!).getByText('Alę')).toBeInTheDocument()
      })
    })

    it('asks nothing and calls nothing until the user answers', async () => {
      await open()
      await correct()

      await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })
      expect(similarCalls()).toHaveLength(0)
      expect(review()).toBeNull()
    })

    it('is not there before a word was corrected, or if the correction is cancelled', async () => {
      await open()
      expect(offer()).toBeNull()

      await userEvent.click(screen.getByText('jćj', { selector: '.word' }))
      await userEvent.keyboard('X{Escape}')

      expect(offer()).toBeNull()
    })

    it('is not there when the word is removed, or the text is the same', async () => {
      await open()

      await userEvent.click(screen.getByText('jćj', { selector: '.word' }))
      await userEvent.keyboard('{Backspace}{Enter}') // removes the token
      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(offer()).toBeNull()
    })

    it('is not there when the correction fails', async () => {
      await open({ correct: () => new Error('The page has changed') })

      await correct()

      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      expect(offer()).toBeNull()
    })

    it('"Nein Danke" closes it, and nothing is asked of the backend', async () => {
      await open()
      await correct()

      await userEvent.click(await screen.findByRole('button', { name: 'Nein Danke' }))

      expect(offer()).toBeNull()
      expect(review()).toBeNull()
      expect(similarCalls()).toHaveLength(0)
    })

    it('Escape closes it too', async () => {
      await open()
      await correct()
      await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })

      await userEvent.keyboard('{Escape}')

      expect(offer()).toBeNull()
    })

    it('a new correction replaces the offer', async () => {
      await open()
      await correct('jćj', 'jéj')
      await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })

      await correct('tćj', 'téj')

      await waitFor(() => expect(within(offer()!).getByText('téj')).toBeInTheDocument())
      expect(screen.getAllByRole('dialog')).toHaveLength(1)
    })

    it('goes with the page when the user leaves for the editions', async () => {
      await open()
      await correct()
      await screen.findByRole('dialog', { name: 'Apply the correction to the entire edition' })

      await userEvent.click(screen.getByRole('button', { name: 'Back to the editions' }))

      expect(offer()).toBeNull()
    })
  })

  describe('the list of the places', () => {
    it('opens with "Apply it to the entire edition" and asks the backend', async () => {
      await openReview()

      expect(similarCalls()).toHaveLength(1)
      expect(similarCalls()[0].method).toBe('GET')
      expect(similarCalls()[0].url).toBe(
        `/editions/${UUID}/similar-misspellings?incorrect_spelling=j%C4%87j&corrected_spelling=j%C3%A9j`,
      )
      expect(offer()).toBeNull() // the small box gives way to the big one
    })

    it('shows the places by page, each misspelling with its suggestion', async () => {
      await openReview()

      expect(await screen.findByText('3 places on 2 pages')).toBeInTheDocument()
      const first = within(screen.getByRole('region', { name: 'Page 1' }))
      expect(first.getByText('samćj')).toBeInTheDocument()
      expect(first.getByText('saméj')).toBeInTheDocument()
      expect(first.getByText('tćj')).toBeInTheDocument()
      expect(first.getByText('téj')).toBeInTheDocument()
      const third = within(screen.getByRole('region', { name: 'Page 3' }))
      expect(third.getByText('bardzićj')).toBeInTheDocument()
      expect(third.getByText('bardziéj')).toBeInTheDocument()
    })

    it('has an Approve button for each place, with its edition, page and offsets', async () => {
      await openReview()

      const button = await screen.findByRole('button', {
        name: 'Approve samćj → saméj on page 1',
      })
      expect(button).toHaveAttribute('data-edition-id', UUID)
      expect(button).toHaveAttribute('data-page', '1')
      expect(button).toHaveAttribute('data-start', '4')
      expect(button).toHaveAttribute('data-end', '9')
      expect(screen.getAllByRole('button', { name: /^Approve / })).toHaveLength(3)
    })

    it('says so when it is loading, and when there is no place', async () => {
      await openReview({ similar: () => [] })

      expect(await screen.findByText('No other place in the edition has this mistake.')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /^Approve / })).toBeNull()
    })

    it('shows the reason when the backend refuses', async () => {
      await openReview({ similar: () => new Error('Edition not found') })

      expect(await screen.findByRole('alert')).toHaveTextContent('Edition not found')
    })

    it('Close, and Escape, close it', async () => {
      await openReview()
      await screen.findByText('3 places on 2 pages')

      await userEvent.click(screen.getByRole('button', { name: 'Close' }))
      expect(review()).toBeNull()
    })

    it('Escape closes it', async () => {
      await openReview()
      await screen.findByText('3 places on 2 pages')

      await userEvent.keyboard('{Escape}')

      expect(review()).toBeNull()
    })

    it('changes nothing by itself', async () => {
      await openReview()
      await screen.findByText('3 places on 2 pages')

      expect(correctionCalls()).toHaveLength(1) // the user's own correction
    })
  })

  describe('approving', () => {
    it('applies the correction to that place alone, with its offsets', async () => {
      await openReview()

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

      await waitFor(() => expect(correctionCalls()).toHaveLength(2))
      expect(correctionCalls()[1].method).toBe('POST')
      expect(correctionCalls()[1].url).toBe(`/editions/${UUID}/pages/1/corrections`)
      expect(correctionCalls()[1].body).toEqual({
        start: 4,
        end: 9,
        before: 'samćj',
        after: 'saméj',
      })
    })

    it('marks the place as approved, and the others stay', async () => {
      await openReview()

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Approve samćj/ })).toBeNull()
      expect(screen.getByRole('button', { name: /Approve tćj/ })).toBeEnabled()
      expect(screen.getByRole('button', { name: /Approve bardzićj/ })).toBeEnabled()
      expect(correctionCalls()).toHaveLength(2) // nothing else was applied
    })

    it('a place on another page does not change the page on the screen', async () => {
      await openReview()
      const text = pageText().textContent

      await userEvent.click(await screen.findByRole('button', { name: /Approve bardzićj/ }))
      await screen.findByText('✓ Approved')

      expect(pageText().textContent).toBe(text)
      expect(calls.filter((c) => c.url === `/editions/${UUID}/pages/3`)).toHaveLength(0)
    })

    it('a place on the page on the screen shows the page again with its new text', async () => {
      await openReview({
        similar: () => [
          { page_number: 0, tokens: [{ text: 'tćj', start: 16, end: 19, suggestion: 'téj' }] },
        ],
      })
      expect(pageText().textContent).toContain('tćj')

      await userEvent.click(await screen.findByRole('button', { name: /Approve tćj/ }))

      await screen.findByText('✓ Approved')
      await waitFor(() => expect(pageText().textContent).toContain('téj'))
      expect(pageText().textContent).not.toContain('tćj')
    })

    describe('the legal spelling', () => {
      const legalCalls = () => calls.filter((c) => c.url.endsWith('/legal-spellings'))

      it('makes the suggestion a legal spelling of the edition too', async () => {
        await openReview()
        const before = legalCalls().length

        await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

        await screen.findByText('✓ Approved')
        expect(legalCalls()).toHaveLength(before + 1)
        expect(legalCalls().at(-1)!.method).toBe('POST')
        expect(legalCalls().at(-1)!.url).toBe(`/editions/${UUID}/legal-spellings`)
        expect(legalCalls().at(-1)!.body).toEqual({ spelling: 'saméj' })
      })

      it('comes after the correction', async () => {
        await openReview()
        const from = calls.length

        await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))
        await screen.findByText('✓ Approved')

        expect(
          calls.slice(from).map((c) => `${c.method} ${c.url}`).filter((c) => /corrections|legal/.test(c)),
        ).toEqual([
          `POST /editions/${UUID}/pages/1/corrections`,
          `POST /editions/${UUID}/legal-spellings`,
        ])
      })

      it('sends each spelling once, however many places have it', async () => {
        await openReview({
          similar: () => [
            {
              page_number: 1,
              tokens: [
                { text: 'tćj', start: 4, end: 7, suggestion: 'téj' },
                { text: 'tćj', start: 20, end: 23, suggestion: 'téj' },
              ],
            },
          ],
        })
        const before = legalCalls().length

        const [first] = await screen.findAllByRole('button', { name: /Approve tćj/ })
        await userEvent.click(first)
        await screen.findByText('✓ Approved')
        await userEvent.click(screen.getByRole('button', { name: /Approve tćj/ }))
        await waitFor(() => expect(screen.getAllByText('✓ Approved')).toHaveLength(2))

        expect(legalCalls()).toHaveLength(before + 1)
      })

      it('sends none for a suggestion of several words, which is not one spelling', async () => {
        await openReview({
          similar: () => [
            { page_number: 1, tokens: [{ text: 'wcałćj', start: 4, end: 10, suggestion: 'w całéj' }] },
          ],
        })
        const before = legalCalls().length

        await userEvent.click(await screen.findByRole('button', { name: /Approve wcałćj/ }))

        await screen.findByText('✓ Approved')
        expect(legalCalls()).toHaveLength(before)
      })

      it('sends none when the correction is refused', async () => {
        await openReview({
          correct: (body, current) =>
            body.before === 'samćj'
              ? new Error('The page has changed')
              : { content: current.content.replace(body.before, body.after) },
        })
        const before = legalCalls().length

        await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))
        await screen.findByRole('alert')

        expect(legalCalls()).toHaveLength(before)
      })

      it('keeps the place corrected when only the legal spelling fails, and says so', async () => {
        await openReview({ legal: (body) => (body.spelling === 'saméj' ? new Error('Edition not found') : undefined) })

        await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

        expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
        expect(
          screen.getByText('Corrected, but not saved as a legal spelling: Edition not found'),
        ).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /Approve samćj/ })).toBeNull()
      })

      it('shows a place of the page on the screen without red for the new spelling', async () => {
        await openReview({
          spell: (text) => wordsOf(text, ['jćj', 'tćj', 'téj']), // the dictionary does not know it
          similar: () => [
            { page_number: 0, tokens: [{ text: 'tćj', start: 16, end: 19, suggestion: 'téj' }] },
          ],
        })

        await userEvent.click(await screen.findByRole('button', { name: /Approve tćj/ }))

        await waitFor(() => expect(pageText().textContent).toContain('téj'))
        expect(
          Array.from(pageText().querySelectorAll('.word.misspelled')).map((w) => w.textContent),
        ).not.toContain('téj')
      })
    })

    it('moves the places that follow it on the same page by the change in length', async () => {
      await openReview({
        similar: () => [
          {
            page_number: 1,
            tokens: [
              { text: 'samćj', start: 4, end: 9, suggestion: 'samejej' }, // 2 longer
              { text: 'tćj', start: 20, end: 23, suggestion: 'téj' },
            ],
          },
          { page_number: 3, tokens: [{ text: 'bardzićj', start: 7, end: 15, suggestion: 'bardziéj' }] },
        ],
      })

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))
      await screen.findByText('✓ Approved')

      const next = screen.getByRole('button', { name: /Approve tćj/ })
      expect(next).toHaveAttribute('data-start', '22')
      expect(next).toHaveAttribute('data-end', '25')
      // another page is not moved
      expect(screen.getByRole('button', { name: /Approve bardzićj/ })).toHaveAttribute(
        'data-start',
        '7',
      )
      await userEvent.click(next)
      await waitFor(() => expect(correctionCalls()).toHaveLength(3))
      expect(correctionCalls()[2].body).toEqual({
        start: 22,
        end: 25,
        before: 'tćj',
        after: 'téj',
      })
    })

    it('shows why a place was refused, and it can be tried again', async () => {
      let refuse = true
      await openReview({
        correct: (body, current) =>
          body.before === 'samćj' && refuse
            ? new Error('The page has changed')
            : { content: current.content.replace(body.before, body.after) },
      })

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      expect(screen.queryByText('✓ Approved')).toBeNull()
      refuse = false
      await userEvent.click(screen.getByRole('button', { name: /Approve samćj/ }))
      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
    })

    it('a refused place does not move the places after it', async () => {
      await openReview({
        correct: (body, current) =>
          body.before === 'samćj'
            ? new Error('The page has changed')
            : { content: current.content.replace(body.before, body.after) },
      })

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))
      await screen.findByRole('alert')

      expect(screen.getByRole('button', { name: /Approve tćj/ })).toHaveAttribute('data-start', '20')
    })

    it('does one approval at a time: the others wait for it', async () => {
      await openReview()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/pages/1/corrections')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )

      await userEvent.click(await screen.findByRole('button', { name: /Approve samćj/ }))

      await waitFor(() =>
        expect(screen.getByRole('button', { name: /Approve tćj/ })).toBeDisabled(),
      )
      expect(screen.getByRole('button', { name: /Approve bardzićj/ })).toBeDisabled()
      finish()
      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Approve tćj/ })).toBeEnabled()
    })
  })
})

describe('correcting a word', () => {
  const TEXT = 'Ala ma kota, a jey tey nie ma.'
  const WRONG = ['jey', 'tey']
  const options = (extra: Options = {}): Options => ({
    page: () => ({ content: xmlOf(TEXT) }),
    spell: (text) => wordsOf(text, WRONG),
    ...extra,
  })
  const word = (text: string, which = 0) =>
    screen.getAllByText(text, { selector: '.word' })[which]
  const editor = () => screen.queryByRole('textbox', { name: /^Correct / })
  const correctionCalls = () => calls.filter((c) => c.url.endsWith('/corrections'))

  async function open(extra: Options = {}) {
    await openPage(options(extra))
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }

  describe('editing in place', () => {
    it('turns the word into a text field when it is clicked', async () => {
      await open()

      await userEvent.click(word('jey'))

      expect(editor()).toBeInTheDocument()
      expect(editor()).toHaveValue('jey')
      expect(editor()).toHaveAccessibleName('Correct "jey"')
    })

    it('has the field focused, with the word selected, ready to be typed over', async () => {
      await open()

      await userEvent.click(word('jey'))

      expect(editor()).toHaveFocus()
      const field = editor() as HTMLInputElement
      expect(field.selectionStart).toBe(0)
      expect(field.selectionEnd).toBe(3)
    })

    it('puts the field where the word was: the text around it stays', async () => {
      await open()

      await userEvent.click(word('jey'))

      // the word is now the field (and, for a misspelled word, its button)
      const field = editor()!.closest('.word-editor')!
      expect(field.previousSibling?.textContent).toBe(' ')
      expect(field.nextSibling?.textContent).toMatch(/^ /)
      // (the join buttons of its neighbours are part of the editor)
      expect(pageText().textContent!.replace(field.textContent!, '')).toBe(
        'Ala ma kota, a  tey nie ma.',
      )
      expect(screen.queryByText('jey', { selector: '.word' })).toBeNull()
    })

    it('works for every word, the correct ones too', async () => {
      await open()

      await userEvent.click(word('kota'))

      expect(editor()).toHaveValue('kota')
    })

    it('edits one word at a time', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(word('tey'))

      expect(screen.getAllByRole('textbox', { name: /^Correct / })).toHaveLength(1)
      expect(editor()).toHaveValue('tey')
    })

    it('Escape cancels: the word is back and nothing is sent', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.keyboard('X{Escape}')

      expect(editor()).toBeNull()
      expect(pageText().textContent).toBe(TEXT)
      expect(correctionCalls()).toEqual([])
    })

    it('leaving the field cancels too', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.type(editor()!, 'X')

      await userEvent.click(document.body)

      expect(editor()).toBeNull()
      expect(pageText().textContent).toBe(TEXT)
      expect(correctionCalls()).toEqual([])
    })

    it('Enter on a word that was not changed sends nothing and closes the field', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.keyboard('{Enter}')

      expect(editor()).toBeNull()
      expect(correctionCalls()).toEqual([])
    })

    it('Enter on an empty field removes the token: it is sent as an empty text', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.clear(editor()!)

      await userEvent.keyboard('{Enter}')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(correctionCalls()[0].body).toMatchObject({ before: 'jey', after: '' })
    })

    it('the keyboard works: Enter on a focused misspelled word starts editing', async () => {
      await open()
      word('jey').focus()

      await userEvent.keyboard('{Enter}')

      expect(editor()).toHaveValue('jey')
    })
  })

  describe('confirming', () => {
    async function correct(wordText: string, to: string, which = 0) {
      await userEvent.click(word(wordText, which))
      await userEvent.keyboard(`${to}{Enter}`)
    }

    it('sends the offsets of the word, what it was and what it is to be', async () => {
      await open()

      await correct('jey', 'jej')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(correctionCalls()[0].method).toBe('POST')
      expect(correctionCalls()[0].url).toBe(`/editions/${UUID}/pages/0/corrections`)
      expect(correctionCalls()[0].body).toEqual({
        start: TEXT.indexOf('jey'),
        end: TEXT.indexOf('jey') + 3,
        before: 'jey',
        after: 'jej',
      })
    })

    it('sends the offsets of the occurrence that was clicked, not the first', async () => {
      await open({
        page: () => ({ content: xmlOf('tey a tey a tey') }),
        spell: (text) => wordsOf(text, ['tey']),
      })
      await waitFor(() => expect(document.querySelectorAll('.word').length).toBe(5))

      await userEvent.click(word('tey', 1))
      await userEvent.keyboard('tej{Enter}')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(correctionCalls()[0].body).toEqual({
        start: 6,
        end: 9,
        before: 'tey',
        after: 'tej',
      })
    })

    it('trims the new text, and allows spaces inside it', async () => {
      await open()

      await correct('jey', '  w całej  ')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(correctionCalls()[0].body).toMatchObject({ before: 'jey', after: 'w całej' })
    })

    it('shows the new text, and checks the spelling of it again', async () => {
      await open({
        spell: (text) => wordsOf(text, text.includes('jey') ? WRONG : ['tey']),
      })
      calls.length = 0

      await correct('jey', 'jej')

      await waitFor(() =>
        expect(pageText().textContent).toBe('Ala ma kota, a jej tey nie ma.'),
      )
      expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
        `POST /editions/${UUID}/pages/0/corrections`,
        // "jey" was misspelled, so its new spelling is also made legal
        `POST /editions/${UUID}/legal-spellings`,
        'POST /tei/text', // the backend extracts the text of the new XML
        'POST /spellcheck', // ... and it is checked again, once
      ])
      expect(calls.at(-1)!.body).toEqual({
        text: 'Ala ma kota, a jej tey nie ma.',
        edition_id: UUID,
      })
    })

    it('the corrected word is no longer red, the others still are', async () => {
      await open({
        spell: (text) => wordsOf(text, WRONG.filter((w) => text.includes(w))),
      })

      await correct('jey', 'jej')

      await waitFor(() =>
        expect(
          Array.from(pageText().querySelectorAll('.word.misspelled')).map(
            (w) => w.textContent,
          ),
        ).toEqual(['tey']),
      )
    })

    it('closes the field after the correction', async () => {
      await open()

      await correct('jey', 'jej')

      await waitFor(() => expect(pageText().textContent).toContain('jej'))
      expect(editor()).toBeNull()
    })

    it('leaves the ready flag alone', async () => {
      await open()

      await correct('jey', 'jej')

      await waitFor(() => expect(pageText().textContent).toContain('jej'))
      expect(calls.filter((c) => c.method === 'PATCH')).toEqual([])
    })

    it('can correct the same word again', async () => {
      await open({ spell: (text) => wordsOf(text, []) })

      await correct('jey', 'jej')
      await waitFor(() => expect(pageText().textContent).toContain('jej'))
      await correct('jej', 'jej2')

      await waitFor(() => expect(pageText().textContent).toContain('jej2'))
      expect(correctionCalls().map((c) => c.body)).toMatchObject([
        { before: 'jey', after: 'jej' },
        { before: 'jej', after: 'jej2' },
      ])
    })

    it('uses the offsets of the new text for the next correction', async () => {
      await open({ spell: (text) => wordsOf(text, []) })

      await correct('jey', 'bardzo długie') // the text gets longer
      await waitFor(() => expect(pageText().textContent).toContain('bardzo długie'))
      await correct('nie', 'tak')

      await waitFor(() => expect(correctionCalls()).toHaveLength(2))
      const text = 'Ala ma kota, a bardzo długie tey nie ma.'
      expect(correctionCalls()[1].body).toEqual({
        start: text.indexOf('nie'),
        end: text.indexOf('nie') + 3,
        before: 'nie',
        after: 'tak',
      })
    })
  })

  describe('while the request runs', () => {
    it('the field cannot be changed and the page cannot be left', async () => {
      await open()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (
            url: string,
            init?: RequestInit,
          ) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/corrections')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )
      await userEvent.click(word('jey'))
      await userEvent.keyboard('jej{Enter}')

      await waitFor(() => expect(editor()).toBeDisabled())
      expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
      finish()
      await waitFor(() => expect(pageText().textContent).toContain('jej'))
    })
  })

  describe('when it fails', () => {
    it('losing the focus while the request runs does not cancel the correction', async () => {
      await open()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (
            url: string,
            init?: RequestInit,
          ) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/corrections')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )
      await userEvent.click(word('jey'))
      await userEvent.keyboard('jej{Enter}')
      await waitFor(() => expect(editor()).toBeDisabled())

      fireEvent.blur(editor()!) // what a browser may do to a disabled field

      expect(editor()).toBeInTheDocument()
      finish()
      await waitFor(() => expect(pageText().textContent).toContain('jej'))
    })

    it('shows the backend reason, and keeps what was typed so it can be tried again', async () => {
      await open({
        correct: () =>
          new Error('The text at 15-18 is not "jey": the page has changed'),
      })

      await userEvent.click(word('jey'))
      await userEvent.keyboard('jej{Enter}')

      expect(await screen.findByRole('alert')).toHaveTextContent('the page has changed')
      expect(editor()).toHaveValue('jej')
      expect(editor()).toBeEnabled()
    })
  })

  describe('with the entities', () => {
    const ETEXT = 'Jan Kowalski mieszka w Paryżu.'
    const entity = (text: string, label: string): Entity => ({
      text,
      label,
      start: ETEXT.indexOf(text),
      end: ETEXT.indexOf(text) + [...text].length,
    })
    const nerButton = () =>
      screen.getByRole('button', { name: 'Named entity recognition' })

    async function recognized(entities: Entity[]) {
      await open({
        page: () => ({ content: xmlOf(ETEXT) }),
        spell: (text) => wordsOf(text, ['Kowalski']),
        ner: () => entities,
      })
      await userEvent.click(nerButton())
      await waitFor(() =>
        expect(pageText().querySelectorAll('mark.entity').length).toBeGreaterThan(0),
      )
    }

    it('the NER view does not show the spelling: no word is marked as misspelled', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])

      const mark = pageText().querySelector('mark.entity')!
      expect(mark.textContent).toBe('Jan Kowalski')
      expect(pageText().querySelectorAll('.word')).toHaveLength(0)
      expect(pageText().querySelectorAll('.misspelled')).toHaveLength(0)
      expect(pageText().textContent).toBe(ETEXT)
    })

    it('the correction view shows the spelling, and not the entities', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])

      await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

      expect(pageText().querySelectorAll('mark')).toHaveLength(0)
      expect(pageText().querySelector('.word.misspelled')?.textContent).toBe('Kowalski')
    })

    it('the entities come back when the NER view is shown again', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])
      await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

      await userEvent.click(nerButton())

      await waitFor(() =>
        expect(pageText().querySelectorAll('mark.entity')).toHaveLength(1),
      )
    })
  })
})

describe('an entity the recognition missed', () => {
  const TEXT = 'Jan Kowalski mieszka w Paryżu od 1840 roku.'
  const FOUND: Entity = {
    text: 'Jan Kowalski',
    label: 'persName',
    start: 0,
    end: 12,
  }
  const nerButton = () =>
    screen.getByRole('button', { name: 'Named entity recognition' })
  const dialog = () =>
    screen.queryByRole('dialog', { name: 'Mark the selected text as a named entity' })
  const dropdown = () => screen.getByRole('combobox', { name: 'Named entity' })
  const tagCalls = () => calls.filter((c) => /\/pages\/\d+\/entities$/.test(c.url))
  const nerCalls = () => calls.filter((c) => c.url === '/ner')
  const marks = () =>
    Array.from(pageText().querySelectorAll('mark.entity')).map((m) => m.textContent)

  async function openNer(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      ner: () => [FOUND],
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))
    await userEvent.click(nerButton())
    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
  }

  /** Select `selected` in the text (where it first is), as with the mouse. */
  function select(selected: string) {
    const box = pageText()
    const from = TEXT.indexOf(selected)
    const to = from + selected.length
    // the text nodes of the text, without the ones in a dialog
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT)
    const range = document.createRange()
    let seen = 0
    let node: Text | null
    while ((node = walker.nextNode() as Text | null)) {
      if (node.parentElement?.closest('[role="dialog"]')) continue
      const end = seen + node.data.length
      if (from >= seen && from < end) range.setStart(node, from - seen)
      if (to > seen && to <= end) range.setEnd(node, to - seen)
      seen = end
    }
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    fireEvent.mouseUp(box)
  }

  afterEach(() => window.getSelection()?.removeAllRanges())

  it('offers a dialog for a selection in the NER view', async () => {
    await openNer()

    select('Paryżu')

    expect(dialog()).toBeInTheDocument()
  })

  it('has a dropdown of the labels, without "Not NER", and a disabled Confirm', async () => {
    await openNer()

    select('Paryżu')

    expect(
      within(dropdown())
        .getAllByRole('option')
        .map((o) => (o as HTMLOptionElement).value),
    ).toEqual([
      '',
      'persName',
      'placeName',
      'orgName',
      'geogName',
      'date',
      'time',
      'ref',
      'bibl',
      'cit',
    ])
    expect(screen.queryByRole('option', { name: 'Not NER' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled()
  })

  it('says what ref, bibl and cit are', async () => {
    await openNer()

    select('Paryżu')

    expect(screen.getByRole('option', { name: 'ref (reference)' })).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: 'bibl (bibliographic information)' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'cit (cited quotation)' })).toBeInTheDocument()
  })

  it.each(['ref', 'bibl', 'cit'])('sends %s to the backend as the label', async (label) => {
    await openNer()

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), label)
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(tagCalls()).toHaveLength(1))
    expect(tagCalls()[0].body).toEqual({
      start: TEXT.indexOf('Paryżu'),
      end: TEXT.indexOf('Paryżu') + 6,
      text: 'Paryżu',
      label,
    })
  })

  it('shows what was marked as a reference as confirmed, like any entity', async () => {
    await openNer()

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'ref')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski', 'Paryżu']))
    expect(pageText().querySelector('mark.confirmed')?.textContent).toBe('Paryżu')
  })

  it('does nothing in the correction view', async () => {
    await openPage({ page: () => ({ content: xmlOf(TEXT) }) })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))

    select('Paryżu')

    expect(dialog()).toBeNull()
  })

  it('sends the selected text, its place and the label to the backend', async () => {
    await openNer()

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(tagCalls()).toHaveLength(1))
    expect(tagCalls()[0].method).toBe('POST')
    expect(tagCalls()[0].url).toBe(`/editions/${UUID}/pages/0/entities`)
    expect(tagCalls()[0].body).toEqual({
      start: TEXT.indexOf('Paryżu'),
      end: TEXT.indexOf('Paryżu') + 6,
      text: 'Paryżu',
      label: 'placeName',
    })
  })

  it('shows it as a confirmed entity with the others, and closes the dialog', async () => {
    await openNer()

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski', 'Paryżu']))
    expect(pageText().querySelectorAll('mark.confirmed')).toHaveLength(1)
    expect(pageText().querySelector('mark.confirmed')?.textContent).toBe('Paryżu')
    expect(dialog()).toBeNull()
    expect(pageText().textContent).toBe(TEXT)
  })

  it('stays when the text is recognized again', async () => {
    await openNer()
    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(marks()).toContain('Paryżu'))

    await userEvent.click(nerButton())

    await waitFor(() => expect(nerCalls()).toHaveLength(2))
    expect(marks()).toEqual(['Jan Kowalski', 'Paryżu'])
    expect(pageText().querySelector('mark.confirmed')?.textContent).toBe('Paryżu')
  })

  it('wins over a recognized entity that it overlaps', async () => {
    await openNer()

    select('Kowalski mieszka')
    await userEvent.selectOptions(dropdown(), 'persName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(marks()).toEqual(['Kowalski mieszka']))
  })

  it('works when nothing was recognized', async () => {
    await openNer({ ner: () => [] }).catch(() => undefined)
    await waitFor(() => expect(nerCalls()).toHaveLength(1))

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(marks()).toEqual(['Paryżu']))
  })

  it('shows the backend error and no entity when the backend refuses', async () => {
    await openNer({ tag: () => new Error('The entity spans paragraphs') })

    select('Paryżu')
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('spans paragraphs')
    expect(marks()).toEqual(['Jan Kowalski'])
  })

  it('closes with Escape, and sends nothing', async () => {
    await openNer()
    select('Paryżu')
    expect(dialog()).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')

    expect(dialog()).toBeNull()
    expect(tagCalls()).toHaveLength(0)
  })

  it('closes when the selection is cleared by a click', async () => {
    await openNer()
    select('Paryżu')

    window.getSelection()!.removeAllRanges()
    fireEvent.mouseUp(pageText())

    expect(dialog()).toBeNull()
  })

  it('works while the dialog of a recognized entity is open', async () => {
    await openNer()
    await userEvent.hover(pageText().querySelector('mark.entity')!)
    expect(screen.getByRole('dialog', { name: 'Named entity: Jan Kowalski' })).toBeInTheDocument()

    select('Paryżu')

    expect(dialog()).toBeInTheDocument()
    await userEvent.selectOptions(dropdown(), 'placeName')
    await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(tagCalls()[0].body).toMatchObject({
      start: TEXT.indexOf('Paryżu'),
      end: TEXT.indexOf('Paryżu') + 6,
    }))
  })
})

describe('entities that are marked in the XML already', () => {
  const TEXT = 'Jan Kowalski mieszka w Paryżu od 1840 roku.'
  const MARKED_XML = xmlOf(
    'Jan Kowalski mieszka w <placeName>Paryżu</placeName> od <date when="1840">1840</date> roku.',
  )
  const nerButton = () =>
    screen.getByRole('button', { name: 'Named entity recognition' })
  const marks = () =>
    Array.from(pageText().querySelectorAll('mark.entity')).map((m) => m.textContent)
  const confirmed = () =>
    Array.from(pageText().querySelectorAll('mark.confirmed')).map((m) => m.textContent)
  const FOUND: Entity = { text: 'Jan Kowalski', label: 'persName', start: 0, end: 12 }

  async function reopen(extra: Options = {}) {
    // as after a refresh: the XML has the tags, nothing is known in the browser
    await openPage({
      page: () => ({ content: MARKED_XML }),
      ner: () => [FOUND],
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))
  }

  it('are shown as confirmed in the NER view, with the recognized ones', async () => {
    await reopen()

    await userEvent.click(nerButton())

    await waitFor(() =>
      expect(marks()).toEqual(['Jan Kowalski', 'Paryżu', '1840']),
    )
    expect(confirmed()).toEqual(['Paryżu', '1840'])
  })

  it('are asked of the backend, for the XML of the page, when the NER view is opened', async () => {
    await reopen()
    calls.length = 0

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toHaveLength(3))
    const asked = calls.filter((c) => c.url === '/tei/entities')
    expect(asked).toHaveLength(1)
    expect(asked[0].body).toEqual({ xml: MARKED_XML })
  })

  it('are not shown in the correction view', async () => {
    await reopen()

    expect(pageText().querySelectorAll('mark')).toHaveLength(0)
    expect(calls.filter((c) => c.url === '/tei/entities')).toHaveLength(0)
  })

  it('are shown whatever tag they have, also one that was not made by the app', async () => {
    await reopen()

    await userEvent.click(nerButton())

    await waitFor(() => expect(confirmed()).toContain('1840'))
  })

  it('win over a recognized entity that they overlap', async () => {
    await reopen({
      ner: () => [{ text: 'w Paryżu od', label: 'placeName', start: 20, end: 31 }],
    })

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Paryżu', '1840']))
  })

  it('have no dialog: they are confirmed', async () => {
    await reopen()
    await userEvent.click(nerButton())
    await waitFor(() => expect(confirmed()).toHaveLength(2))

    await userEvent.hover(pageText().querySelectorAll('mark.confirmed')[0])

    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('are shown when the recognition fails', async () => {
    await reopen({ ner: () => new Error('The model is not installed') })

    await userEvent.click(nerButton())

    expect(await screen.findByRole('alert')).toHaveTextContent('not installed')
    await waitFor(() => expect(confirmed()).toEqual(['Paryżu', '1840']))
  })

  it('are left out, and the rest works, when the backend cannot read them', async () => {
    await reopen({ marked: () => new Error('The XML is not TEI') })

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
    expect(confirmed()).toEqual([])
  })

  it('a new entity is read back from the XML that the backend returns', async () => {
    await reopen()
    await userEvent.click(nerButton())
    await waitFor(() => expect(marks()).toHaveLength(3))
    calls.length = 0

    // confirm the recognized "Jan Kowalski"
    await userEvent.hover(pageText().querySelector('mark.entity')!)
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm' }),
    )

    await waitFor(() => expect(confirmed()).toEqual(['Jan Kowalski', 'Paryżu', '1840']))
    expect(calls.filter((c) => c.url === '/tei/entities')).toHaveLength(1)
  })
})

describe('the scan', () => {
  const scan = () => screen.getByRole('img', { name: 'Scan of page 0' })
  const spinner = () => screen.queryByRole('progressbar', { name: 'Loading the scan' })

  it('shows a loading indicator until the image has loaded', async () => {
    await openPage()
    await screen.findByRole('img')

    expect(spinner()).toBeInTheDocument()
    expect(spinner()).toHaveTextContent('Loading the scan')

    fireEvent.load(scan())

    expect(spinner()).toBeNull()
    expect(scan()).toBeVisible()
  })

  it('asks the browser to fetch the scan first, and does not push it down', async () => {
    await openPage()
    await screen.findByRole('img')

    expect(scan()).toHaveAttribute('fetchpriority', 'high')
    // the spinner is laid over the half, so it is not in the way of the scan
    expect(spinner()!.classList.contains('scan-loading')).toBe(true)
    expect(getComputedStyle(spinner()!).height).toBe('0px')
  })

  it('shows it again for the scan of the next page, until that has loaded', async () => {
    await openPage({
      page: (n) => ({ scan_uri: `https://polona.pl/scan-${n}.jpg` }),
    })
    fireEvent.load(await screen.findByRole('img', { name: 'Scan of page 0' }))
    expect(spinner()).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    fireEvent.load(await screen.findByRole('img', { name: 'Scan of page 1' }))
    expect(spinner()).toBeNull()
  })

  it('is back for the next page while its scan is still coming', async () => {
    await openPage({
      page: (n) => ({ scan_uri: `https://polona.pl/scan-${n}.jpg` }),
    })
    fireEvent.load(await screen.findByRole('img', { name: 'Scan of page 0' }))

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await screen.findByRole('img', { name: 'Scan of page 1' })
    expect(spinner()).toBeInTheDocument()
  })

  it('stays while the image has not loaded, whatever else is done to the page', async () => {
    await openPage()
    await screen.findByRole('img')

    await userEvent.click(screen.getByRole('button', { name: 'Zoom in' }))

    expect(spinner()).toBeInTheDocument()
  })

  it('says so, and has no spinner, when the image cannot be loaded', async () => {
    await openPage()
    await screen.findByRole('img')

    fireEvent.error(scan())

    expect(spinner()).toBeNull()
    expect(await screen.findByRole('alert')).toHaveTextContent('scan could not be loaded')
  })

  it('has no spinner for a page without a scan', async () => {
    await openPage({ page: () => ({ scan_uri: null }) })
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    expect(spinner()).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })
})

describe('scrolling the scan and the text together', () => {
  /**
   * Give an element a size and a scroll position, which jsdom does not have. As
   * in a browser, a change of the position is followed by a scroll event.
   */
  function layout(element: Element, scrollHeight: number, clientHeight: number) {
    let scrollTop = 0
    Object.defineProperties(element, {
      scrollHeight: { value: scrollHeight, configurable: true },
      clientHeight: { value: clientHeight, configurable: true },
      scrollTop: {
        get: () => scrollTop,
        set: (value: number) => {
          if (value === scrollTop) return
          scrollTop = value
          element.dispatchEvent(new Event('scroll'))
        },
        configurable: true,
      },
    })
  }
  const scanHalf = () => document.querySelectorAll('.viewer-half')[0] as HTMLElement

  async function open() {
    await openPage()
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    layout(scanHalf(), 2000, 500) // 1500 to scroll
    layout(pageText(), 1000, 400) // 600 to scroll
  }

  it('scrolling the scan scrolls the text by the same part of the way', async () => {
    await open()

    scanHalf().scrollTop = 750 // half way
    expect(pageText().scrollTop).toBe(300)

    scanHalf().scrollTop = 1500 // to the end
    expect(pageText().scrollTop).toBe(600)

    scanHalf().scrollTop = 0
    expect(pageText().scrollTop).toBe(0)
  })

  it('scrolling the text scrolls the scan by the same part of the way', async () => {
    await open()

    pageText().scrollTop = 300 // half way
    expect(scanHalf().scrollTop).toBe(750)

    pageText().scrollTop = 600 // to the end
    expect(scanHalf().scrollTop).toBe(1500)

    pageText().scrollTop = 0
    expect(scanHalf().scrollTop).toBe(0)
  })

  it('does not bounce: the one that was moved does not move the other back', async () => {
    await open()
    // 1/3 of the text is 200 of 600, and 1/3 of the scan is 500 of 1500
    const scans: number[] = []
    scanHalf().addEventListener('scroll', () => scans.push(scanHalf().scrollTop))

    pageText().scrollTop = 200

    expect(scanHalf().scrollTop).toBe(500)
    expect(pageText().scrollTop).toBe(200)
    expect(scans).toEqual([500]) // moved once, and not again by the echo
  })

  it('keeps following after an echo, in either direction', async () => {
    await open()

    pageText().scrollTop = 300
    scanHalf().scrollTop = 300 // a fifth of the way
    expect(pageText().scrollTop).toBe(120)
    pageText().scrollTop = 60 // a tenth of the way
    expect(scanHalf().scrollTop).toBe(150)
  })

  it('also moves the XML view, which is another box', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'XML' }))
    const xml = screen.getByRole('region', { name: 'Page XML' })
    layout(xml, 3000, 400) // 2600 to scroll

    scanHalf().scrollTop = 300 // a fifth of the way
    expect(xml.scrollTop).toBe(520)

    xml.scrollTop = 1300 // half way
    expect(scanHalf().scrollTop).toBe(750)
  })

  it('does nothing when the text has nothing to scroll', async () => {
    await open()
    layout(pageText(), 300, 400)

    scanHalf().scrollTop = 750

    expect(pageText().scrollTop).toBe(0)
  })

  it('does not move the scan when it has nothing to scroll', async () => {
    await open()
    layout(scanHalf(), 400, 500)

    pageText().scrollTop = 300

    expect(scanHalf().scrollTop).toBe(0)
  })
})

describe('joining two tokens that the OCR split', () => {
  const TEXT = 'Ala jest abso lutely tu, a\nnoc'
  const joinCalls = () => calls.filter((c) => c.url.endsWith('/joins'))
  const previous = () => screen.queryByRole('button', { name: /Join with previous/ })
  const next = () => screen.queryByRole('button', { name: /Join with next/ })

  async function open(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      spell: (text) => wordsOf(text),
      ...extra,
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }
  const edit = (word: string) => userEvent.click(screen.getByText(word, { selector: '.word' }))

  it('offers to join a word with the next one when only a space is between', async () => {
    await open()

    await edit('abso')

    expect(next()).toBeInTheDocument()
    expect(previous()).toBeInTheDocument()
  })

  it('sends the two words, and shows the joined text', async () => {
    await open()
    await edit('abso')

    await userEvent.click(next()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].method).toBe('POST')
    expect(joinCalls()[0].url).toBe(`/editions/${UUID}/pages/0/joins`)
    expect(joinCalls()[0].body).toEqual({ start: 9, end: 20, text: 'abso lutely' })
    await waitFor(() =>
      expect(pageText().textContent).toBe('Ala jest absolutely tu, a\nnoc'),
    )
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('joins with the previous word from the second one', async () => {
    await open()
    await edit('lutely')

    await userEvent.click(previous()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].body).toEqual({ start: 9, end: 20, text: 'abso lutely' })
  })

  it('records no correction and changes nothing else', async () => {
    await open()
    await edit('abso')
    await userEvent.click(next()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(calls.filter((c) => c.url.endsWith('/corrections'))).toHaveLength(0)
  })

  it('does not offer it across a line break or punctuation', async () => {
    await open()

    await edit('a') // "a" is followed by a line break, and preceded by ", "
    expect(next()).toBeNull()
    expect(previous()).toBeNull()
    await userEvent.keyboard('{Escape}')

    await edit('tu') // "tu" is followed by a comma
    expect(next()).toBeNull()
  })

  it('offers nothing for the first word of the page, as to the previous one', async () => {
    await open()

    await edit('Ala')

    expect(previous()).toBeNull()
    expect(next()).toBeInTheDocument()
  })

  it('shows the error when the backend refuses, and keeps the text', async () => {
    await open({ join: () => new Error('The page has changed') })
    await edit('abso')

    await userEvent.click(next()!)

    expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
    expect(pageText().textContent).toBe(TEXT)
  })

  it('is not offered outside the correction view', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))

    expect(document.querySelectorAll('.word')).toHaveLength(0)
    expect(next()).toBeNull()
  })
})

describe('starting a new line', () => {
  const TEXT = 'Ala jest abso lutely tu, a\nnoc'
  const splitCalls = () => calls.filter((c) => c.url.endsWith('/line-splits'))
  const newLine = () => screen.queryByRole('button', { name: /New line here/ })

  async function open(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      spell: (text) => wordsOf(text),
      ...extra,
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }
  const edit = (word: string) => userEvent.click(screen.getByText(word, { selector: '.word' }))

  it('is offered for a word that has another one before it on the line', async () => {
    await open()

    await edit('abso')

    expect(newLine()).toBeInTheDocument()
  })

  it('sends the word before and the word, and shows the text with the new line', async () => {
    await open()
    await edit('lutely')

    await userEvent.click(newLine()!)

    await waitFor(() => expect(splitCalls()).toHaveLength(1))
    expect(splitCalls()[0].method).toBe('POST')
    expect(splitCalls()[0].url).toBe(`/editions/${UUID}/pages/0/line-splits`)
    expect(splitCalls()[0].body).toEqual({ start: 9, end: 20, text: 'abso lutely' })
    await waitFor(() =>
      expect(pageText().textContent).toBe('Ala jest abso\nlutely tu, a\nnoc'),
    )
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('records no correction', async () => {
    await open()
    await edit('lutely')
    await userEvent.click(newLine()!)

    await waitFor(() => expect(splitCalls()).toHaveLength(1))
    expect(calls.filter((c) => c.url.endsWith('/corrections'))).toHaveLength(0)
  })

  it('checks the spelling again for the new text', async () => {
    await open()
    await edit('lutely')
    const before = calls.filter((c) => c.url === '/spellcheck').length

    await userEvent.click(newLine()!)

    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/spellcheck')).toHaveLength(before + 1),
    )
  })

  it('is not offered for the first word of a line, or after punctuation', async () => {
    await open()

    await edit('Ala') // the first word of the page
    expect(newLine()).toBeNull()
    await userEvent.keyboard('{Escape}')

    await edit('a') // after "tu, ": punctuation is between
    expect(newLine()).toBeNull()
    await userEvent.keyboard('{Escape}')

    await edit('noc') // the first word of the second line
    expect(newLine()).toBeNull()
  })

  it('shows the error when the backend refuses, and keeps the text', async () => {
    await open({ split: () => new Error('The page has changed') })
    await edit('lutely')

    await userEvent.click(newLine()!)

    expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
    expect(pageText().textContent).toBe(TEXT)
  })

  it('is not offered outside the correction view', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))

    expect(document.querySelectorAll('.word')).toHaveLength(0)
    expect(newLine()).toBeNull()
  })
})

describe('removing a line break', () => {
  const TEXT = 'Ala jest abso\nlutely tu, a\nnoc\n\nNowy akapit'
  const joinCalls = () => calls.filter((c) => c.url.endsWith('/line-joins'))
  const before = () => screen.queryByRole('button', { name: /↑ Remove line break/ })
  const after = () => screen.queryByRole('button', { name: /Remove line break ↓/ })

  async function open(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      spell: (text) => wordsOf(text),
      ...extra,
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }
  const edit = (word: string) => userEvent.click(screen.getByText(word, { selector: '.word' }))

  it('is offered before the first word of a line, and after the last one', async () => {
    await open()

    await edit('lutely') // the first word of the second line
    expect(before()).toBeInTheDocument()
    expect(after()).toBeNull()
    await userEvent.keyboard('{Escape}')

    await edit('abso') // the last word of the first line
    expect(after()).toBeInTheDocument()
    expect(before()).toBeNull()
  })

  it('is not offered for a word in the middle of a line', async () => {
    await open()

    await edit('jest')

    expect(before()).toBeNull()
    expect(after()).toBeNull()
  })

  it('is called "Remove empty line" across an empty line between paragraphs', async () => {
    await open()

    await edit('noc') // followed by an empty line
    expect(after()).toBeNull()
    expect(screen.getByRole('button', { name: /Remove empty line ↓/ })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')

    await edit('Nowy') // preceded by one
    expect(before()).toBeNull()
    expect(screen.getByRole('button', { name: /↑ Remove empty line/ })).toBeInTheDocument()
  })

  it('sends the two words with the empty line, and shows one line break less', async () => {
    await open()
    await edit('Nowy')

    await userEvent.click(screen.getByRole('button', { name: /↑ Remove empty line/ }))

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].body).toEqual({ start: 27, end: 36, text: 'noc\n\nNowy' })
    await waitFor(() =>
      expect(pageText().textContent).toBe(
        'Ala jest abso\nlutely tu, a\nnoc\nNowy akapit',
      ),
    )
  })

  it('removes the empty line below a page that starts with one token', async () => {
    await openPage({
      page: () => ({ content: xmlOf('315\n\nbłędy zbyt') }),
      spell: (text) => wordsOf(text),
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
    await edit('315')

    await userEvent.click(screen.getByRole('button', { name: /Remove empty line ↓/ }))

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].body).toEqual({ start: 0, end: 10, text: '315\n\nbłędy' })
  })

  it('sends the two words with the line break, and shows the joined line', async () => {
    await open()
    await edit('lutely')

    await userEvent.click(before()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].method).toBe('POST')
    expect(joinCalls()[0].url).toBe(`/editions/${UUID}/pages/0/line-joins`)
    expect(joinCalls()[0].body).toEqual({ start: 9, end: 20, text: 'abso\nlutely' })
    await waitFor(() =>
      expect(pageText().textContent).toBe('Ala jest abso lutely tu, a\nnoc\n\nNowy akapit'),
    )
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('does the same from the last word of the line above', async () => {
    await open()
    await edit('abso')

    await userEvent.click(after()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(joinCalls()[0].body).toEqual({ start: 9, end: 20, text: 'abso\nlutely' })
  })

  it('records no correction', async () => {
    await open()
    await edit('lutely')
    await userEvent.click(before()!)

    await waitFor(() => expect(joinCalls()).toHaveLength(1))
    expect(calls.filter((c) => c.url.endsWith('/corrections'))).toHaveLength(0)
  })

  it('shows the error when the backend refuses, and keeps the text', async () => {
    await open({ joinLines: () => new Error('The page has changed') })
    await edit('lutely')

    await userEvent.click(before()!)

    expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
    expect(pageText().textContent).toBe(TEXT)
  })

  it('is not offered outside the correction view', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))

    expect(document.querySelectorAll('.word')).toHaveLength(0)
    expect(before()).toBeNull()
  })
})

describe('a page without text', () => {
  const field = () => screen.getByRole('textbox', { name: 'Text to add to the empty page' })
  const addButton = () => screen.getByRole('button', { name: 'Add text' })
  const tokenCalls = () => calls.filter((c) => c.url.endsWith('/tokens'))

  async function open(extra: Options = {}) {
    await openPage({ page: () => ({ content: xmlOf('') }), ...extra })
    await screen.findByRole('textbox', { name: 'Text to add to the empty page' })
  }

  it('has a field and an Add text button instead of the double-click hint', async () => {
    await open()

    expect(addButton()).toBeDisabled()
    expect(screen.getByText(/This page has no text/)).toBeInTheDocument()
    expect(screen.queryByText(/Double-click a place/)).toBeNull()
  })

  it('sends the text as a token at position 0, and shows it as the first line', async () => {
    await open()

    await userEvent.type(field(), 'LIST   SZÓSTY')
    await userEvent.click(addButton())

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].method).toBe('POST')
    expect(tokenCalls()[0].url).toBe(`/editions/${UUID}/pages/0/tokens`)
    expect(tokenCalls()[0].body).toEqual({ position: 0, text: 'LIST SZÓSTY' })
    await waitFor(() => expect(pageText().textContent).toBe('LIST SZÓSTY'))
    expect(screen.queryByRole('textbox', { name: 'Text to add to the empty page' })).toBeNull()
    expect(screen.getByText(/Double-click a place/)).toBeInTheDocument()
  })

  it('adds with Enter too', async () => {
    await open()

    await userEvent.type(field(), 'X{Enter}')

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].body).toEqual({ position: 0, text: 'X' })
  })

  it('sends nothing for a field with only spaces', async () => {
    await open()

    await userEvent.type(field(), '   ')

    expect(addButton()).toBeDisabled()
    expect(tokenCalls()).toHaveLength(0)
  })

  it('shows the error when the backend refuses, and keeps the field', async () => {
    await open({ token: () => new Error('There is no place for text') })

    await userEvent.type(field(), 'X')
    await userEvent.click(addButton())

    expect(await screen.findByRole('alert')).toHaveTextContent('There is no place for text')
    expect(field()).toBeInTheDocument()
  })

  it('is not offered for a page whose text could not be read', async () => {
    await openPage({ page: () => ({ content: xmlOf('') }), tei: () => new Error('The XML is empty') })
    await screen.findByText(/The text of this page cannot be shown/)

    expect(screen.queryByRole('textbox', { name: 'Text to add to the empty page' })).toBeNull()
  })

  it('is not offered for a page that has text', async () => {
    await openPage({ page: () => ({ content: xmlOf('Ala') }), spell: (text) => wordsOf(text) })
    await waitFor(() => expect(pageText().textContent).toBe('Ala'))

    expect(screen.queryByRole('textbox', { name: 'Text to add to the empty page' })).toBeNull()
  })
})

describe('the page number on the other pages', () => {
  const PAGE_NUMBER = {
    start: 0,
    end: 4,
    open: '<fw type="pageNum">',
    close: '</fw>',
  }
  const match = (page: number, text: string, fits = true, context = 'Ala ma kota') => ({
    page_number: page,
    start: 0,
    end: text.length,
    text,
    number: Number(text),
    context,
    fits_sequence: fits,
  })
  const FOUND: SimilarPageNumbers = {
    example: match(0, '8'),
    where: 'first',
    offset: 3,
    candidates: [
      match(1, '9', true, 'Ciekawość względem'),
      match(2, '10', true, 'Nic łatwiejszego'),
      match(3, '99', false, 'TREŚĆ'),
    ],
  }
  const find = () => screen.getByRole('button', { name: 'Find this on other pages' })
  const review = () =>
    screen.queryByRole('dialog', { name: 'The same page number on the other pages' })
  const approveAll = () => screen.getByRole('button', { name: /^Approve all/ })
  const include = (page: number) =>
    screen.getByRole('checkbox', { name: `Include page ${page} in Approve all` })
  const sectionCalls = () => calls.filter((c) => /\/pages\/\d+\/sections$/.test(c.url))
  const numberCalls = () => calls.filter((c) => c.url.endsWith('/similar-page-numbers'))

  async function openStructure(extra: Options = {}) {
    await openPage({
      sections: () => [PAGE_NUMBER],
      similarNumbers: () => FOUND,
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    await userEvent.click(screen.getByRole('button', { name: 'Structure' }))
  }
  async function openReview(extra: Options = {}) {
    await openStructure(extra)
    await waitFor(() => expect(find()).toBeEnabled())
    await userEvent.click(find())
    await screen.findByText(/pages?, \d+ of them/)
  }

  describe('the button', () => {
    it('is in the structure view, and only there', async () => {
      await openPage({ sections: () => [PAGE_NUMBER] })
      await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
      expect(screen.queryByRole('button', { name: 'Find this on other pages' })).toBeNull()

      await userEvent.click(screen.getByRole('button', { name: 'Structure' }))

      expect(find()).toBeInTheDocument()
    })

    it('can be used when a page number is marked on the page', async () => {
      await openStructure()

      await waitFor(() => expect(find()).toBeEnabled())
    })

    it('can not be used when no page number is marked: it says to mark one first', async () => {
      await openStructure({ sections: () => [] })

      expect(find()).toBeDisabled()
      expect(find()).toHaveAttribute('title', 'Mark the page number on this page first')
    })

    it('can not be used when only another section is marked', async () => {
      await openStructure({
        sections: () => [{ ...PAGE_NUMBER, open: '<fw type="header">' }],
      })

      expect(find()).toBeDisabled()
    })

    it('does nothing until it is clicked: the backend is not asked', async () => {
      await openStructure()
      await waitFor(() => expect(find()).toBeEnabled())

      expect(numberCalls()).toHaveLength(0)
      expect(review()).toBeNull()
    })
  })

  describe('the list of the pages', () => {
    it('asks the backend about the page that is on the screen', async () => {
      await openReview()

      expect(numberCalls()).toHaveLength(1)
      expect(numberCalls()[0].method).toBe('GET')
      expect(numberCalls()[0].url).toBe(`/editions/${UUID}/pages/0/similar-page-numbers`)
    })

    it('says what the example is, and how many pages it found', async () => {
      await openReview()

      expect(
        screen.getByText(/The example is page 0: “8” on the first line, so the printed number is the number of the page less 3/),
      ).toBeInTheDocument()
      expect(
        screen.getByText('3 pages, 2 of them with a number that follows the numbering'),
      ).toBeInTheDocument()
    })

    it('shows each page with its number and the line next to it', async () => {
      await openReview()

      const rows = screen.getAllByRole('listitem')
      expect(rows).toHaveLength(3)
      expect(within(rows[0]).getByText('Page 1')).toBeInTheDocument()
      expect(within(rows[0]).getByText('“9”')).toBeInTheDocument()
      expect(within(rows[0]).getByText('Ciekawość względem')).toBeInTheDocument()
      expect(within(rows[0]).getByText('fits')).toBeInTheDocument()
    })

    it('shows that a number does not follow the numbering, and what it should be', async () => {
      await openReview()

      const row = screen.getAllByRole('listitem')[2]
      expect(within(row).getByText('“99”')).toBeInTheDocument()
      expect(
        within(row).getByText('does not follow the numbering: expected 0'),
      ).toBeInTheDocument()
      expect(row).toHaveClass('misfit')
    })

    it('has an Approve button for each page, with its edition, page and offsets', async () => {
      await openReview()

      const button = screen.getByRole('button', { name: 'Approve “10” on page 2' })
      expect(button).toHaveAttribute('data-edition-id', UUID)
      expect(button).toHaveAttribute('data-page', '2')
      expect(button).toHaveAttribute('data-start', '0')
      expect(button).toHaveAttribute('data-end', '2')
    })

    it('has the pages that follow the numbering in "Approve all", and the others out', async () => {
      await openReview()

      expect(include(1)).toBeChecked()
      expect(include(2)).toBeChecked()
      expect(include(3)).not.toBeChecked()
      expect(approveAll()).toHaveTextContent('Approve all (2)')
    })

    it('marks nothing by itself', async () => {
      await openReview()

      expect(sectionCalls()).toHaveLength(0)
    })

    it('says so when no page is like it', async () => {
      await openStructure({ similarNumbers: () => ({ ...FOUND, candidates: [] }) })
      await waitFor(() => expect(find()).toBeEnabled())

      await userEvent.click(find())

      expect(
        await screen.findByText('No other page has a page number like it.'),
      ).toBeInTheDocument()
    })

    it('shows the reason when the backend refuses', async () => {
      await openStructure({
        similarNumbers: () => new Error('The page number must be alone on its line'),
      })
      await waitFor(() => expect(find()).toBeEnabled())

      await userEvent.click(find())

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The page number must be alone on its line',
      )
    })

    it('Close and Escape close it', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Close' }))
      expect(review()).toBeNull()

      await userEvent.click(find())
      await screen.findByText(/pages?, \d+ of them/)
      await userEvent.keyboard('{Escape}')
      expect(review()).toBeNull()
    })
  })

  describe('approving a page', () => {
    it('marks the page number on that page alone, with its offsets', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Approve “10” on page 2' }))

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].method).toBe('POST')
      expect(sectionCalls()[0].url).toBe(`/editions/${UUID}/pages/2/sections`)
      expect(sectionCalls()[0].body).toEqual({
        start: 0,
        end: 2,
        text: '10',
        section: 'page_number',
      })
    })

    it('marks the page as approved, and the others stay', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Approve “10” on page 2' }))

      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Approve “10” on page 2' })).toBeNull()
      expect(screen.getByRole('button', { name: 'Approve “9” on page 1' })).toBeEnabled()
      expect(sectionCalls()).toHaveLength(1)
    })

    it('can approve a page that is not in "Approve all"', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Approve “99” on page 3' }))

      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].url).toBe(`/editions/${UUID}/pages/3/sections`)
    })

    it('shows why a page was refused, and it can be tried again', async () => {
      let refuse = true
      await openReview({
        section: (_body, current) =>
          refuse ? new Error('The page has changed') : { content: current.content },
      })

      await userEvent.click(screen.getByRole('button', { name: 'Approve “9” on page 1' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      refuse = false
      await userEvent.click(screen.getByRole('button', { name: 'Approve “9” on page 1' }))
      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
    })
  })

  describe('approving all', () => {
    it('marks the page number on every page that is in, in the order of the pages', async () => {
      await openReview()

      await userEvent.click(approveAll())

      await waitFor(() => expect(sectionCalls()).toHaveLength(2))
      expect(sectionCalls().map((c) => c.url)).toEqual([
        `/editions/${UUID}/pages/1/sections`,
        `/editions/${UUID}/pages/2/sections`,
      ])
      expect(sectionCalls().map((c) => c.body)).toEqual([
        { start: 0, end: 1, text: '9', section: 'page_number' },
        { start: 0, end: 2, text: '10', section: 'page_number' },
      ])
    })

    it('does not take a page that is out of it', async () => {
      await openReview()

      await userEvent.click(approveAll())
      await waitFor(() => expect(screen.getAllByText('✓ Approved')).toHaveLength(2))

      expect(sectionCalls().some((c) => c.url.includes('/pages/3/'))).toBe(false)
      expect(screen.getByRole('button', { name: 'Approve “99” on page 3' })).toBeEnabled()
    })

    it('has a page taken out of it, with its box, and counts the rest', async () => {
      await openReview()

      await userEvent.click(include(1))

      expect(include(1)).not.toBeChecked()
      expect(approveAll()).toHaveTextContent('Approve all (1)')
      await userEvent.click(approveAll())
      await waitFor(() => expect(sectionCalls()).toHaveLength(1))
      expect(sectionCalls()[0].url).toBe(`/editions/${UUID}/pages/2/sections`)
    })

    it('can take a page that was out into it', async () => {
      await openReview()

      await userEvent.click(include(3))

      expect(approveAll()).toHaveTextContent('Approve all (3)')
      await userEvent.click(approveAll())
      await waitFor(() => expect(sectionCalls()).toHaveLength(3))
    })

    it('has Include all, Include none and Include only those that follow the numbering', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))
      expect(approveAll()).toHaveTextContent('Approve all (3)')
      await userEvent.click(screen.getByRole('button', { name: 'Include none' }))
      expect(approveAll()).toHaveTextContent('Approve all (0)')
      expect(approveAll()).toBeDisabled()
      await userEvent.click(
        screen.getByRole('button', { name: 'Include only those that follow the numbering' }),
      )
      expect(approveAll()).toHaveTextContent('Approve all (2)')
    })

    it('has nothing left to approve when it is done', async () => {
      await openReview()

      await userEvent.click(approveAll())

      await waitFor(() => expect(approveAll()).toHaveTextContent('Approve all (0)'))
      expect(approveAll()).toBeDisabled()
      expect(screen.getByText(/2 approved/)).toBeInTheDocument()
    })

    it('goes on after a page that was refused, and says which', async () => {
      await openReview({
        section: (body, current) =>
          body.text === '9'
            ? new Error('The page has changed')
            : { content: current.content },
      })

      await userEvent.click(approveAll())

      await waitFor(() => expect(sectionCalls()).toHaveLength(2))
      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      expect(screen.getAllByText('✓ Approved')).toHaveLength(1)
      expect(screen.getByText(/1 failed/)).toBeInTheDocument()
      // the page that failed is still to approve, and Approve all takes just that
      expect(approveAll()).toHaveTextContent('Approve all (1)')
    })

    it('shows how far it is while it works, and nothing else can be clicked meanwhile', async () => {
      await openReview()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/pages/1/sections')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )

      await userEvent.click(approveAll())

      expect(await screen.findByText('Approving 0 of 2 …')).toBeInTheDocument()
      expect(approveAll()).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Approve “99” on page 3' })).toBeDisabled()
      expect(include(2)).toBeDisabled()
      finish()
      await waitFor(() => expect(screen.getAllByText('✓ Approved')).toHaveLength(2))
      expect(screen.queryByText(/^Approving /)).toBeNull()
    })
  })
})

describe('adding a token the OCR missed', () => {
  const TEXT = 'Ala ma kota\nnoc'
  const dialog = () => screen.queryByRole('dialog', { name: 'Add a text token' })
  const field = () => screen.getByRole('textbox', { name: 'Text to add' })
  const addButton = () => within(dialog()!).getByRole('button', { name: 'Add' })
  const tokenCalls = () => calls.filter((c) => c.url.endsWith('/tokens'))

  /** The browser's answer to "where is the cursor if I click here". */
  function caretAtOffset(offset: number, node?: Text) {
    const target = node ?? (pageText().firstChild as Text)
    Object.defineProperty(document, 'caretPositionFromPoint', {
      value: () => ({ offsetNode: target, offset }),
      configurable: true,
    })
  }
  const doubleClick = (element: Element = pageText()) =>
    fireEvent.doubleClick(element, { clientX: 20, clientY: 10 })

  async function open(extra: Options = {}) {
    await openPage({ page: () => ({ content: xmlOf(TEXT) }), ...extra })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))
  }

  afterEach(() => {
    // (jsdom has none of its own)
    delete (document as { caretPositionFromPoint?: unknown }).caretPositionFromPoint
  })

  it('offers a box to add a token when a place in the text is double-clicked', async () => {
    await open()
    caretAtOffset(4)

    doubleClick()

    expect(dialog()).toBeInTheDocument()
    expect(field()).toHaveFocus()
    expect(addButton()).toBeDisabled()
  })

  it('shows a hint for it', async () => {
    await open()

    expect(screen.getByText(/Double-click a place in the text/)).toBeInTheDocument()
  })

  it('sends the place and the token to the backend', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()

    await userEvent.type(field(), 'XX')
    await userEvent.click(addButton())

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].method).toBe('POST')
    expect(tokenCalls()[0].url).toBe(`/editions/${UUID}/pages/0/tokens`)
    expect(tokenCalls()[0].body).toEqual({ position: 4, text: 'XX' })
  })

  it('shows the text that the backend returns, and closes the box', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()

    await userEvent.type(field(), 'XX')
    await userEvent.click(addButton())

    await waitFor(() => expect(pageText().textContent).toBe('Ala XX ma kota\nnoc'))
    expect(dialog()).toBeNull()
  })

  it('also adds with Enter', async () => {
    await open()
    caretAtOffset(11)
    doubleClick()

    await userEvent.type(field(), 'XX{Enter}')

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].body).toEqual({ position: 11, text: 'XX' })
  })

  it('counts the place in code points, as the backend does, after emoji', async () => {
    await openPage({ page: () => ({ content: xmlOf('😀 Ala ma') }) })
    await waitFor(() => expect(pageText().textContent).toBe('😀 Ala ma'))
    caretAtOffset(7) // "😀 Ala " is 7 UTF-16 units, 6 code points
    doubleClick()

    await userEvent.type(field(), 'X{Enter}')

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].body).toEqual({ position: 6, text: 'X' })
  })

  it('accepts several words, like "LIST SZÓSTY"', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()

    await userEvent.type(field(), 'LIST SZÓSTY')
    expect(addButton()).toBeEnabled()
    await userEvent.click(addButton())

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].body).toEqual({ position: 4, text: 'LIST SZÓSTY' })
    await waitFor(() =>
      expect(pageText().textContent).toBe('Ala LIST SZÓSTY ma kota\nnoc'),
    )
  })

  it('sends the words with single spaces, and without spaces at the ends', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()

    await userEvent.type(field(), '  LIST   SZÓSTY  {Enter}')

    await waitFor(() => expect(tokenCalls()).toHaveLength(1))
    expect(tokenCalls()[0].body).toEqual({ position: 4, text: 'LIST SZÓSTY' })
  })

  it('does not accept nothing, or only spaces', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()

    expect(addButton()).toBeDisabled()
    await userEvent.type(field(), '   {Enter}')
    expect(addButton()).toBeDisabled()

    expect(tokenCalls()).toHaveLength(0)
  })

  it('closes with Escape, and sends nothing', async () => {
    await open()
    caretAtOffset(4)
    doubleClick()
    await userEvent.type(field(), 'XX')

    await userEvent.keyboard('{Escape}')

    expect(dialog()).toBeNull()
    expect(tokenCalls()).toHaveLength(0)
  })

  it('shows the backend error and leaves the text as it was', async () => {
    await open({ token: () => new Error('There is no place for text there') })
    caretAtOffset(4)
    doubleClick()

    await userEvent.type(field(), 'XX{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent('no place for text')
    expect(pageText().textContent).toBe(TEXT)
  })

  it('does nothing when the browser cannot say where the click was', async () => {
    await open()

    doubleClick()

    expect(dialog()).toBeNull()
  })

  it('does nothing on a word: a word is edited, not added to', async () => {
    await open({
      spell: (text) => wordsOf(text, ['ma']),
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
    caretAtOffset(1)

    doubleClick(screen.getAllByText('ma', { selector: '.word' })[0])

    expect(dialog()).toBeNull()
  })

  it('works on a place between the words of a text that is split into words', async () => {
    await open({
      spell: (text) => wordsOf(text, []),
    })
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
    // the space after "Ala": a text node of its own, between two words
    const gap = Array.from(pageText().childNodes).find(
      (node) => node.nodeType === Node.TEXT_NODE && node.textContent === ' ',
    ) as Text
    caretAtOffset(1, gap)

    doubleClick()

    expect(dialog()).toBeInTheDocument()
  })

  it('is not offered in the NER view', async () => {
    await open()
    await userEvent.click(
      screen.getByRole('button', { name: 'Named entity recognition' }),
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Named entity recognition' })).toBeEnabled(),
    )
    caretAtOffset(4)

    doubleClick()

    expect(dialog()).toBeNull()
  })

  it('is not offered in the text view', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Text' }))
    caretAtOffset(4)

    doubleClick()

    expect(dialog()).toBeNull()
  })
})

describe('legal spellings', () => {
  const TEXT = 'Ala ma kota, a jey tey nie ma. Jey!'
  const WRONG = ['jey', 'Jey', 'tey']
  const options = (extra: Options = {}): Options => ({
    page: () => ({ content: xmlOf(TEXT) }),
    spell: (text) => wordsOf(text, WRONG),
    ...extra,
  })
  const word = (text: string, which = 0) =>
    screen.getAllByText(text, { selector: '.word' })[which]
  const editor = () => screen.queryByRole('textbox', { name: /^Correct / })
  const legalButton = () => screen.queryByRole('button', { name: 'Legal spelling' })
  const legalCalls = () => calls.filter((c) => c.url.endsWith('/legal-spellings'))
  const correctionCalls = () => calls.filter((c) => c.url.endsWith('/corrections'))
  const misspelled = () =>
    Array.from(pageText().querySelectorAll('.word.misspelled')).map((w) => w.textContent)

  async function open(extra: Options = {}) {
    await openPage(options(extra))
    await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
  }

  describe('the button', () => {
    it('is offered next to the field of a misspelled word', async () => {
      await open()

      await userEvent.click(word('jey'))

      expect(editor()).toBeInTheDocument()
      expect(legalButton()).toBeInTheDocument()
    })

    it('is not offered for a word that is spelled correctly', async () => {
      await open()

      await userEvent.click(word('kota'))

      expect(editor()).toBeInTheDocument()
      expect(legalButton()).toBeNull()
    })

    it('is not there until the word is clicked', async () => {
      await open()

      expect(legalButton()).toBeNull()
    })

    it('sends the spelling to legal-spellings of the edition', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(legalCalls()).toHaveLength(1))
      expect(legalCalls()[0].method).toBe('POST')
      expect(legalCalls()[0].url).toBe(`/editions/${UUID}/legal-spellings`)
      expect(legalCalls()[0].body).toEqual({ spelling: 'jey' })
    })

    it('sends the spelling as it is in the text, whatever is in the field', async () => {
      await open()
      await userEvent.click(word('tey'))
      await userEvent.type(editor()!, 'xyz') // the field is being edited...

      await userEvent.click(legalButton()!)

      // ...but the button is about the spelling that was clicked
      await waitFor(() => expect(legalCalls()).toHaveLength(1))
      expect(legalCalls()[0].body).toEqual({ spelling: 'tey' })
    })

    it('does not send a correction, and the text stays as it is', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(misspelled()).not.toContain('jey'))
      expect(correctionCalls()).toEqual([])
      expect(pageText().textContent).toBe(TEXT)
    })

    it('the word is no longer red, the others still are', async () => {
      await open()
      expect(misspelled()).toEqual(['jey', 'tey', 'Jey'])
      await userEvent.click(word('tey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(misspelled()).toEqual(['jey', 'Jey']))
    })

    it('asks the backend again about the same text, for the edition', async () => {
      await open()
      await userEvent.click(word('jey'))
      calls.length = 0

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(misspelled()).not.toContain('jey'))
      expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
        `POST /editions/${UUID}/legal-spellings`,
        'POST /spellcheck', // the backend counts it as correct now
      ])
      expect(calls[1].body).toEqual({ text: TEXT, edition_id: UUID })
    })

    it('does not decide what is red: it shows what the backend says', async () => {
      // the backend still says "jey" is wrong (e.g. it ignored the spelling)
      await open({ spell: (text) => wordsOf(text, ['jey', 'tey', 'Jey']) })
      await userEvent.click(word('jey'))
      const fake = (fetch as unknown as {
        getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
      }).getMockImplementation()
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (url === '/spellcheck') {
            const text = JSON.parse(String(init!.body)).text
            return reply(true, 200, { words: wordsOf(text, ['jey', 'tey', 'Jey']) })
          }
          return fake(url, init)
        }),
      )

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(legalCalls()).toHaveLength(1))
      await waitFor(() => expect(editor()).toBeNull())
      expect(misspelled()).toContain('jey') // as the backend says
    })

    it('closes the field and shows the new count', async () => {
      await open()
      expect(misspelled()).toHaveLength(3)
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(editor()).toBeNull())
      // "jey" and its capital form "Jey" are both legal now
      expect(misspelled()).toHaveLength(1)
    })

    it('makes the same spelling legal everywhere in the text, capital forms too', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(misspelled()).toEqual(['tey']))
    })

    it('can be reached with the keyboard: Tab from the field, Enter on the button', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.tab()
      expect(legalButton()).toHaveFocus()
      await userEvent.keyboard('{Enter}')

      await waitFor(() => expect(legalCalls()).toHaveLength(1))
    })

    it('does not cancel the editing when it is clicked: the field is not left first', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      // the click reached the button: the request was sent
      await waitFor(() => expect(legalCalls()).toHaveLength(1))
    })

    it('leaving both the field and the button still cancels', async () => {
      await open()
      await userEvent.click(word('jey'))

      await userEvent.click(document.body)

      expect(editor()).toBeNull()
      expect(legalButton()).toBeNull()
      expect(legalCalls()).toEqual([])
    })

    it('is disabled, with the field, while the request runs', async () => {
      await open()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/legal-spellings')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )
      await userEvent.click(word('jey'))
      await userEvent.click(legalButton()!)

      await waitFor(() => expect(legalButton()).toBeDisabled())
      expect(editor()).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
      finish()
      await waitFor(() => expect(misspelled()).not.toContain('jey'))
    })

    it('shows the backend reason when it is refused, and the word stays red', async () => {
      await open({ legal: () => new Error('Edition not found') })
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      expect(await screen.findByRole('alert')).toHaveTextContent('Edition not found')
      expect(editor()).toBeInTheDocument() // can be tried again
      expect(legalButton()).toBeEnabled()
      await userEvent.keyboard('{Escape}')
      expect(misspelled()).toContain('jey') // still red
    })

    it('shows the reason when the spelling cannot be checked again, after it was saved', async () => {
      await open()
      await userEvent.click(word('jey'))
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
        }
      ).getMockImplementation()
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (url === '/spellcheck') throw new Error('Network down')
          return original(url, init)
        }),
      )

      await userEvent.click(legalButton()!)

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The spelling cannot be checked: Network down',
      )
      expect(legalCalls()).toHaveLength(1) // it was saved
      expect(pageText().textContent).toBe(TEXT)
    })

    it('leaves the entities and the page as they are', async () => {
      await open({ ner: () => [] })

      await userEvent.click(word('jey'))
      await userEvent.click(legalButton()!)

      await waitFor(() => expect(misspelled()).not.toContain('jey'))
      expect(
        calls.filter((c) => c.method === 'PATCH' || c.url.includes('/entities')),
      ).toEqual([])
    })
  })

  describe('editing a misspelled word and pressing Enter', () => {
    async function edit(wordText: string, to: string, which = 0) {
      await userEvent.click(word(wordText, which))
      await userEvent.keyboard(`${to}{Enter}`)
    }

    it('sends the correction, and then the new spelling to legal-spellings', async () => {
      await open()
      calls.length = 0

      await edit('jey', 'jeyy')

      await waitFor(() => expect(legalCalls()).toHaveLength(1))
      expect(calls.map((c) => `${c.method} ${c.url}`).slice(0, 2)).toEqual([
        `POST /editions/${UUID}/pages/0/corrections`,
        `POST /editions/${UUID}/legal-spellings`,
      ])
      expect(correctionCalls()[0].body).toMatchObject({ before: 'jey', after: 'jeyy' })
      expect(legalCalls()[0].body).toEqual({ spelling: 'jeyy' })
    })

    it('the edited word is no longer red, even though the dictionary does not know it', async () => {
      await open({ spell: (text) => wordsOf(text, ['jey', 'tey', 'Jey', 'jeyy']) })

      await edit('jey', 'jeyy')

      await waitFor(() => expect(pageText().textContent).toContain('jeyy'))
      // the edited word is a word, not red; the others that were wrong still are
      expect(word('jeyy')).not.toHaveClass('misspelled')
      expect(misspelled()).toEqual(['tey', 'Jey'])
    })

    it('checks the text once, after both requests', async () => {
      await open()
      calls.length = 0

      await edit('jey', 'jeyy')

      await waitFor(() => expect(pageText().textContent).toContain('jeyy'))
      expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
        `POST /editions/${UUID}/pages/0/corrections`,
        `POST /editions/${UUID}/legal-spellings`,
        'POST /tei/text',
        'POST /spellcheck',
      ])
    })

    it('sends the spelling as it was confirmed: trimmed', async () => {
      await open()

      await edit('tey', '  tej  ')

      await waitFor(() => expect(legalCalls()).toHaveLength(1))
      expect(legalCalls()[0].body).toEqual({ spelling: 'tej' })
    })

    it('sends no legal spelling for an edit into several words, which is not one', async () => {
      await open()

      await edit('tey', 'te j')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      await waitFor(() => expect(pageText().textContent).toContain('te j'))
      expect(legalCalls()).toEqual([])
    })

    it('sends nothing when the word is not changed', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.keyboard('{Enter}')

      expect(correctionCalls()).toEqual([])
      expect(legalCalls()).toEqual([])
    })

    it('sends no legal spelling when a word is removed', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.clear(editor()!)
      await userEvent.keyboard('{Enter}')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(legalCalls()).toEqual([])
    })

    it('sends no legal spelling when the correction fails', async () => {
      await open({ correct: () => new Error('The page has changed') })

      await edit('jey', 'jeyy')

      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      expect(legalCalls()).toEqual([])
      expect(editor()).toHaveValue('jeyy') // kept, to try again
    })

    it('shows the corrected text, and says what failed, when only the legal spelling fails', async () => {
      await open({ legal: () => new Error('Edition not found') })

      await edit('jey', 'jeyy')

      await waitFor(() => expect(pageText().textContent).toContain('jeyy'))
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The word was corrected, but not saved as a legal spelling: Edition not found',
      )
      expect(editor()).toBeNull()
    })

    it('is a plain correction for a word that was spelled correctly: nothing is made legal', async () => {
      await open()
      calls.length = 0

      await edit('kota', 'kotu')

      await waitFor(() => expect(pageText().textContent).toContain('kotu'))
      expect(legalCalls()).toEqual([])
      expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
        `POST /editions/${UUID}/pages/0/corrections`,
        'POST /tei/text',
        'POST /spellcheck',
      ])
    })

    it('shows no legal button for the correct word that is being edited', async () => {
      await open()

      await userEvent.click(word('kota'))

      expect(legalButton()).toBeNull()
    })

    it('is disabled meanwhile, like any correction', async () => {
      await open()
      const original = (
        fetch as unknown as {
          getMockImplementation: () => (url: string, init?: RequestInit) => Promise<unknown>
        }
      ).getMockImplementation()
      let finish: () => void = () => {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (!url.endsWith('/legal-spellings')) return original(url, init)
          await new Promise<void>((resolve) => (finish = resolve))
          return original(url, init)
        }),
      )

      await edit('jey', 'jeyy')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      await waitFor(() => expect(editor()).toBeDisabled()) // still waiting for the legal one
      finish()
      await waitFor(() => expect(pageText().textContent).toContain('jeyy'))
    })
  })

  describe('the legal spellings are asked for with every check', () => {
    it('sends the edition with the spell check of every page', async () => {
      await open({
        page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Inny tekst') }),
      })

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(pageText().textContent).toBe('Inny tekst'))
      expect(
        calls
          .filter((c) => c.url === '/spellcheck')
          .map((c) => (c.body as { edition_id: string }).edition_id),
      ).toEqual([UUID, UUID])
    })

    it('what was made legal stays so on the next page of the edition', async () => {
      await open({
        page: (n) => ({ content: xmlOf(n === 0 ? TEXT : 'Znowu jey tutaj') }),
      })
      await userEvent.click(word('jey'))
      await userEvent.click(legalButton()!)
      await waitFor(() => expect(misspelled()).not.toContain('jey'))

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(pageText().textContent).toBe('Znowu jey tutaj'))
      expect(misspelled()).toEqual([]) // "jey" is legal here too (the backend says so)
    })
  })
})

describe('page view', () => {
  it('shows the scan on the left and the text on the right', async () => {
    await openPage()

    const image = await screen.findByRole('img')
    expect(image).toHaveAttribute('src', SCAN)
    const [left, right] = Array.from(document.querySelectorAll('.viewer-half'))
    expect(left).toContainElement(image)
    expect(right).toContainElement(pageText())
    expect(screen.queryByLabelText('Edition UUID')).not.toBeInTheDocument()
  })

  it('has a right sidebar with three tool buttons', async () => {
    await openPage()
    await screen.findByRole('img')

    const sidebar = screen.getByRole('complementary', { name: 'Tools' })
    const buttons = within(sidebar)
      .getAllByRole('button')
      .filter((b) => !b.closest('.view-switch'))

    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Correction',
      'Structure',
      'Named entity recognition',
    ])
    for (const button of buttons) {
      expect(button).toHaveAttribute(
        'data-tooltip',
        button.getAttribute('aria-label'),
      )
      expect(button.querySelector('svg')).not.toBeNull()
    }
    expect(sidebar.previousElementSibling).toBe(
      document.querySelectorAll('.viewer-half')[1],
    )
  })

  describe('the active view', () => {
    const pressed = () =>
      ['Correction', 'Structure', 'Named entity recognition'].filter(
        (name) =>
          screen.getByRole('button', { name }).getAttribute('aria-pressed') === 'true',
      )

    it('is the correction view on entering the page, with the spelling checked', async () => {
      await openPage({ spell: (text) => wordsOf(text, ['text']) })
      await waitFor(() =>
        expect(pageText().querySelectorAll('.word.misspelled').length).toBeGreaterThan(0),
      )

      expect(pressed()).toEqual(['Correction'])
    })

    it('changes to Structure and to Named entity recognition, and back', async () => {
      await openPage({ ner: () => [] })
      await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

      await userEvent.click(screen.getByRole('button', { name: 'Structure' }))
      expect(pressed()).toEqual(['Structure'])

      await userEvent.click(
        screen.getByRole('button', { name: 'Named entity recognition' }),
      )
      await waitFor(() => expect(pressed()).toEqual(['Named entity recognition']))

      await userEvent.click(screen.getByRole('button', { name: 'Correction' }))
      expect(pressed()).toEqual(['Correction'])
    })
  })

  describe('the Text and XML buttons', () => {
    const textButton = () => screen.getByRole('button', { name: 'Text' })
    const xmlButton = () => screen.getByRole('button', { name: 'XML' })
    const xmlBox = () => screen.getByRole('region', { name: 'Page XML' })

    async function openText() {
      await openPage({ spell: (text) => wordsOf(text, ['text']) })
      await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    }

    it('XML shows the XML of the page as it is, and is the active view', async () => {
      await openText()

      await userEvent.click(xmlButton())

      expect(xmlBox().textContent).toBe(xmlOf('text of page 0'))
      expect(xmlButton()).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByRole('button', { name: 'Correction' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
    })

    it('Text shows the text that the backend takes from the XML, with no spelling marks', async () => {
      await openText()
      expect(pageText().querySelector('.word.misspelled')).not.toBeNull()

      await userEvent.click(textButton())

      expect(pageText().textContent).toBe('text of page 0')
      expect(pageText().querySelectorAll('.word, mark')).toHaveLength(0)
      expect(textButton()).toHaveAttribute('aria-pressed', 'true')
    })

    it('puts the markup of the XML on yellow, in smaller type', async () => {
      await openText()

      await userEvent.click(xmlButton())

      const tag = xmlBox().querySelector('.xml-markup') as HTMLElement
      expect(tag.textContent).toBe('<?xml version="1.0"?>')
      expect(parseFloat(getComputedStyle(tag).fontSize)).toBeLessThan(
        parseFloat(getComputedStyle(xmlBox()).fontSize),
      )
    })

    it('switch back and forth without any request', async () => {
      await openText()
      const before = calls.length

      await userEvent.click(xmlButton())
      await userEvent.click(textButton())
      await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

      expect(calls).toHaveLength(before)
      expect(pageText().querySelector('.word.misspelled')).not.toBeNull()
    })

    it.each([['Next page'], ['Previous page']])(
      'the view goes back to Correction on %s',
      async (button) => {
        await openPage({ page: (n) => ({ content: xmlOf(`text of page ${n}`) }) }, 1)
        await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))
        await userEvent.click(xmlButton())

        await userEvent.click(screen.getByRole('button', { name: button }))

        await waitFor(() => expect(pageText().textContent).toMatch(/^text of page [02]$/))
        expect(screen.getByRole('button', { name: 'Correction' })).toHaveAttribute(
          'aria-pressed',
          'true',
        )
        expect(xmlButton()).toHaveAttribute('aria-pressed', 'false')
      },
    )

    it('XML shows what the backend returned after a change, not what the browser made', async () => {
      await openPage({
        spell: (text) => wordsOf(text, ['jey']),
        page: () => ({ content: xmlOf('ala jey') }),
        correct: () => ({ content: xmlOf('what the backend says') }),
      })
      await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))

      await userEvent.click(screen.getAllByText('jey', { selector: '.word' })[0])
      await userEvent.keyboard('jej{Enter}')

      await waitFor(() => expect(pageText().textContent).toContain('what the backend says'))
      await userEvent.click(xmlButton())
      expect(xmlBox().textContent).toBe(xmlOf('what the backend says'))
    })
  })

  it('has the Text and XML buttons in the sidebar, below the tools', async () => {
    await openPage()
    await screen.findByRole('img')

    const sidebar = screen.getByRole('complementary', { name: 'Tools' })
    const group = within(sidebar).getByRole('group', { name: 'Show' })

    expect(
      within(group).getAllByRole('button').map((b) => b.textContent),
    ).toEqual(['Text', 'XML'])
    expect(sidebar.lastElementChild).toBe(group)
  })

  it('zooms the scan in and out within limits', async () => {
    await openPage()
    const image = await screen.findByRole('img')
    const zoomIn = screen.getByRole('button', { name: 'Zoom in' })
    const zoomOut = screen.getByRole('button', { name: 'Zoom out' })

    expect(image).toHaveStyle({ width: '100%' })
    expect(zoomOut).toBeDisabled()

    await userEvent.click(zoomIn)
    expect(image).toHaveStyle({ width: '150%' })
    expect(screen.getByText('150%')).toBeInTheDocument()
    expect(zoomOut).toBeEnabled()

    for (let i = 0; i < 10; i++) await userEvent.click(zoomIn)
    expect(image).toHaveStyle({ width: '500%' })
    expect(zoomIn).toBeDisabled()

    await userEvent.click(zoomOut)
    expect(image).toHaveStyle({ width: '450%' })
  })

  it('shows only the text when a page has no scan', async () => {
    await openPage({ page: () => ({ scan_uri: null }) })

    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})

describe('page navigation', () => {
  const open = (number: number) => openPage({}, number)

  it('has a button that goes back to the editions, the main page', async () => {
    await open(1)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))

    const home = screen.getByRole('button', { name: 'Back to the editions' })
    expect(home.querySelector('svg')).not.toBeNull()
    expect(home).toHaveAttribute('data-tooltip', 'Back to the editions')
    expect(getComputedStyle(home).borderRadius).toBe('50%')

    await userEvent.click(home)

    expect(await screen.findByRole('navigation', { name: 'Editions' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Page text' })).toBeNull()
    expect(window.location.pathname).toBe('/')
    expect(calledUrls()).toContain('GET /editions?page=1')
  })

  it('has round buttons with an arrow, and the name only as a tooltip', async () => {
    await open(1)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))

    const previous = screen.getByRole('button', { name: 'Previous page' })
    const next = screen.getByRole('button', { name: 'Next page' })

    for (const button of [previous, next]) {
      expect(button.querySelector('svg')).not.toBeNull() // the arrow
      expect(button.textContent).toBe('') // no words on the button itself
      expect(button).toHaveAttribute('data-tooltip', button.getAttribute('aria-label'))
      expect(parseFloat(getComputedStyle(button).borderRadius)).toBeGreaterThan(0)
    }
    expect(getComputedStyle(previous).borderRadius).toBe('50%')
  })

  it('has round buttons in the sidebar too', async () => {
    await open(1)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))

    const buttons = Array.from(document.querySelectorAll('.sidebar-button'))
    expect(buttons.length).toBeGreaterThan(0)
    for (const button of buttons) {
      expect(getComputedStyle(button).borderRadius).toBe('50%')
    }
  })

  it('disables previous page on page 0', async () => {
    await open(0)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
  })

  it('goes to the next page: fetches it, gets its text, updates the URL', async () => {
    await open(0)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
    calls.length = 0

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))
    expect(calledUrls()).toEqual([
      `GET /editions/${UUID}/pages/1`,
      'POST /tei/text',
      'POST /spellcheck',
    ])
    expect(calls[1].body).toEqual({ xml: xmlOf('text of page 1') })
    expect(window.location.pathname).toBe(`/editions/${UUID}/pages/1`)
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })

  it('goes to the previous page', async () => {
    await open(2)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 2'))

    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }))

    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))
    expect(window.location.pathname).toBe(`/editions/${UUID}/pages/1`)
  })

  it('stays on the page and shows an error when there is no next page', async () => {
    await open(3)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 3'))

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Page not found')
    expect(pageText().textContent).toBe('text of page 3')
    expect(window.location.pathname).toBe(`/editions/${UUID}/pages/3`)
  })
})



describe('the missing hyphens of a page', () => {
  const TEXT = 'Ala przeczy\ntać kota, i do\nmu wrócił, a na\nwet nie'
  const hyphen = (
    text: string,
    next: string,
    start: number,
    ambiguous = false,
  ): MissingHyphen => ({
    start,
    end: start + [...text].length,
    text,
    suggestion: `${text.replace(/[^\p{L}]+$/u, '')}-`,
    next_word: next,
    joined: `${text.replace(/[^\p{L}]+$/u, '')}${next}`,
    ambiguous,
  })
  const FOUND = [
    hyphen('przeczy', 'tać', 4),
    hyphen('do', 'mu', 23, true),
    hyphen('na', 'wet', 40, true),
  ]
  const find = () => screen.getByRole('button', { name: 'Find missing hyphens' })
  const review = () =>
    screen.queryByRole('region', { name: 'The missing hyphens on this page' })
  const approveAll = () => screen.getByRole('button', { name: /^Approve all/ })
  const include = (word: string, next: string) =>
    screen.getByRole('checkbox', { name: `Include ${word} ${next} in Approve all` })
  const approveOne = (suggestion: string, next: string) =>
    screen.getByRole('button', { name: `Approve ${suggestion} before ${next}` })
  const getCalls = () => calls.filter((c) => c.url.endsWith('/missing-hyphens'))
  const getPageCalls = () => calls.filter((c) => c.method === 'GET').map((c) => c.url)
  const postCalls = () =>
    calls.filter((c) => c.method === 'POST' && c.url.endsWith('/hyphens'))

  async function open(extra: Options = {}) {
    await openPage({
      page: () => ({ content: xmlOf(TEXT) }),
      hyphens: () => FOUND,
      ...extra,
    })
    await waitFor(() => expect(pageText().textContent).toBe(TEXT))
  }
  async function openReview(extra: Options = {}) {
    await open(extra)
    await userEvent.click(find())
    await screen.findByText(/places?, \d+ of them sure/)
  }

  describe('the button', () => {
    it('is in the correction view, and only there', async () => {
      await open()
      expect(find()).toBeEnabled()

      await userEvent.click(screen.getByRole('button', { name: 'Structure' }))

      expect(screen.queryByRole('button', { name: 'Find missing hyphens' })).toBeNull()
    })

    it('does nothing until it is clicked: the backend is not asked', async () => {
      await open()

      expect(getCalls()).toHaveLength(0)
      expect(review()).toBeNull()
    })

    it('can not be used on a page without text', async () => {
      await openPage({ page: () => ({ content: xmlOf('') }) })
      await waitFor(() => expect(find()).toBeDisabled())
    })

    it('asks the backend for this page only', async () => {
      await open()

      await userEvent.click(find())

      await waitFor(() => expect(getCalls()).toHaveLength(1))
      expect(getCalls()[0].url).toBe(`/editions/${UUID}/pages/0/missing-hyphens`)
    })
  })

  describe('the box', () => {
    it('shows each line end: the word, the next word and the word with the hyphen', async () => {
      await openReview()

      expect(review()).toBeInTheDocument()
      const items = within(review()!).getAllByRole('listitem')
      expect(items).toHaveLength(3)
      expect(items[0].textContent).toContain('przeczy')
      expect(items[0].textContent).toContain('tać')
      expect(items[0].textContent).toContain('przeczy-')
    })

    it('shows the word that both halves make together, to decide on', async () => {
      await openReview()

      const items = within(review()!).getAllByRole('listitem')
      expect(items[0].textContent).toContain('(joined: przeczytać)')
      expect(items[1].textContent).toContain('(joined: domu)')
    })

    it('shows the joined word without the stray sign', async () => {
      await open({ hyphens: () => [hyphen('przeczy,', 'tać', 4)] })
      await userEvent.click(find())
      await screen.findByText(/places?, \d+ of them sure/)

      expect(review()!.textContent).toContain('(joined: przeczytać)')
    })

    it('counts the places and the sure ones', async () => {
      await openReview()

      expect(screen.getByText('3 places, 1 of them sure')).toBeInTheDocument()
    })

    it('takes the sure ones by default, and not those where both parts are words', async () => {
      await openReview()

      expect(include('przeczy', 'tać')).toBeChecked()
      expect(include('do', 'mu')).not.toBeChecked()
      expect(include('na', 'wet')).not.toBeChecked()
      expect(approveAll()).toHaveTextContent('Approve all (1)')
    })

    it('says that the ambiguous ones are not sure', async () => {
      await openReview()

      const items = within(review()!).getAllByRole('listitem')
      expect(items[1].textContent).toContain('both parts are words')
      expect(items[0].textContent).not.toContain('both parts are words')
    })

    it('says so when there is none', async () => {
      await open({ hyphens: () => [] })
      await userEvent.click(find())

      expect(await screen.findByText('No missing hyphen found on this page.')).toBeInTheDocument()
    })

    it('shows the reason when the backend refuses', async () => {
      await open({ hyphens: () => new Error('The XML of the page cannot be read') })
      await userEvent.click(find())

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The XML of the page cannot be read',
      )
    })

    it('has buttons to include all, none, or only the sure ones', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))
      expect(approveAll()).toHaveTextContent('Approve all (3)')
      await userEvent.click(screen.getByRole('button', { name: 'Include none' }))
      expect(approveAll()).toHaveTextContent('Approve all (0)')
      expect(approveAll()).toBeDisabled()
      await userEvent.click(screen.getByRole('button', { name: 'Include only the sure ones' }))
      expect(approveAll()).toHaveTextContent('Approve all (1)')
    })

    it('closes with the Close button', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Close' }))

      expect(review()).toBeNull()
    })

    it('does not close with Escape: it is not a box over the page', async () => {
      await openReview()

      await userEvent.keyboard('{Escape}')

      expect(review()).toBeInTheDocument()
    })

    it('is not a modal box: the text stays in sight and can be used', async () => {
      await openReview()

      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.querySelector('.review-backdrop')).toBeNull()
      expect(pageText().textContent).toBe(TEXT)
      // the text is above the panel, in the same half of the screen
      const half = pageText().closest('.text-half')!
      expect(half.contains(review())).toBe(true)
    })

    it('folds to its header, which hides the list, and shows it again', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Fold' }))

      expect(review()).toBeInTheDocument()
      expect(within(review()!).queryAllByRole('listitem')).toHaveLength(0)
      expect(screen.queryByRole('button', { name: /^Approve all/ })).toBeNull()

      await userEvent.click(screen.getByRole('button', { name: 'Show the list' }))

      expect(within(review()!).getAllByRole('listitem')).toHaveLength(3)
    })

    it('keeps what was included when it is folded and shown again', async () => {
      await openReview()
      await userEvent.click(include('do', 'mu'))
      await userEvent.click(screen.getByRole('button', { name: 'Fold' }))
      await userEvent.click(screen.getByRole('button', { name: 'Show the list' }))

      expect(include('do', 'mu')).toBeChecked()
    })

    it('is gone when the user goes to another page', async () => {
      await openReview()

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      await waitFor(() => expect(getPageCalls()).toContain(`/editions/${UUID}/pages/1`))
      expect(review()).toBeNull()
    })

    it('changes nothing until the user approves', async () => {
      await openReview()

      expect(postCalls()).toHaveLength(0)
    })
  })

  describe('showing the place in the text', () => {
    const pointed = () =>
      [...document.querySelectorAll('.pointed')].map((e) => e.textContent)
    const row = (n: number) => within(review()!).getAllByRole('listitem')[n]

    async function openWithWords() {
      await openReview({ spell: (text) => wordsOf(text) })
      await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
    }

    it('marks the two words of a place while it is pointed at', async () => {
      await openWithWords()
      expect(pointed()).toEqual([])

      await userEvent.hover(row(0))

      expect(pointed()).toEqual(['przeczy', 'tać'])
    })

    it('marks the stray sign of a place too', async () => {
      const found = [hyphen('przeczy,', 'tać', 4)]
      await openPage({
        hyphens: () => found,
        page: () => ({ content: xmlOf('Ala przeczy,\ntać kota') }),
        // (the sign is a token of its own, as the backend has it)
        spell: () => [
          { text: 'Ala', start: 0, end: 3, correct: true },
          { text: 'przeczy', start: 4, end: 11, correct: false },
          { text: ',', start: 11, end: 12, correct: true },
          { text: 'tać', start: 13, end: 16, correct: false },
          { text: 'kota', start: 17, end: 21, correct: true },
        ],
      })
      await waitFor(() => expect(document.querySelectorAll('.word').length).toBeGreaterThan(0))
      await userEvent.click(find())
      await screen.findByText(/places?, \d+ of them sure/)

      await userEvent.hover(row(0))

      expect(pointed().join('')).toBe('przeczy,tać')
    })

    it('stops marking when the pointer leaves', async () => {
      await openWithWords()
      await userEvent.hover(row(1))
      expect(pointed()).toEqual(['do', 'mu'])

      await userEvent.unhover(row(1))

      expect(pointed()).toEqual([])
    })

    it('marks a place when it is focused with the keyboard', async () => {
      await openWithWords()

      within(row(2)).getByRole('button', { name: /^Approve/ }).focus()

      await waitFor(() => expect(pointed()).toEqual(['na', 'wet']))
    })

    it('marks nothing once the panel is closed', async () => {
      await openWithWords()
      await userEvent.hover(row(0))

      await userEvent.click(screen.getByRole('button', { name: 'Close' }))

      expect(pointed()).toEqual([])
    })

    it('does not mark an approved place', async () => {
      await openWithWords()
      await userEvent.click(approveOne('przeczy-', 'tać'))
      await screen.findByText('✓ Approved')

      await userEvent.hover(row(0))

      expect(pointed()).toEqual([])
    })
  })

  describe('approving one place', () => {
    it('sends that place alone, with the offsets the button carries', async () => {
      await openReview()
      const button = approveOne('przeczy-', 'tać')
      expect(button).toHaveAttribute('data-edition-id', UUID)
      expect(button).toHaveAttribute('data-page', '0')
      expect(button).toHaveAttribute('data-start', '4')
      expect(button).toHaveAttribute('data-end', '11')

      await userEvent.click(button)

      await waitFor(() => expect(postCalls()).toHaveLength(1))
      expect(postCalls()[0].url).toBe(`/editions/${UUID}/pages/0/hyphens`)
      expect(postCalls()[0].body).toEqual({
        fixes: [{ start: 4, end: 11, before: 'przeczy', after: 'przeczy-' }],
      })
    })

    it('shows the page with the hyphen, and marks the place approved', async () => {
      await openReview()

      await userEvent.click(approveOne('przeczy-', 'tać'))

      await waitFor(() =>
        expect(pageText().textContent).toBe(TEXT.replace('przeczy\n', 'przeczy-\n')),
      )
      expect(await screen.findByText('✓ Approved')).toBeInTheDocument()
      expect(screen.getByText(/1 approved/)).toBeInTheDocument()
    })

    it('can be done for an ambiguous place, which is not in Approve all', async () => {
      await openReview()

      await userEvent.click(approveOne('do-', 'mu'))

      await waitFor(() => expect(postCalls()).toHaveLength(1))
      expect(postCalls()[0].body).toEqual({
        fixes: [{ start: 23, end: 25, before: 'do', after: 'do-' }],
      })
    })

    it('moves the places after it by the hyphen that was added', async () => {
      await openReview()
      await userEvent.click(approveOne('przeczy-', 'tać'))
      await screen.findByText('✓ Approved')

      expect(approveOne('do-', 'mu')).toHaveAttribute('data-start', '24')
      expect(approveOne('do-', 'mu')).toHaveAttribute('data-end', '26')
      expect(approveOne('na-', 'wet')).toHaveAttribute('data-start', '41')
    })

    it('moves the places by the difference of length when a stray sign is replaced', async () => {
      const found = [hyphen('przeczy,', 'tać', 4), hyphen('do', 'mu', 24, true)]
      await openReview({ hyphens: () => found })
      await userEvent.click(approveOne('przeczy-', 'tać'))
      await screen.findByText('✓ Approved')

      // the comma became the hyphen: the page is as long as before
      expect(approveOne('do-', 'mu')).toHaveAttribute('data-start', '24')
    })

    it('does not move the places before it', async () => {
      await openReview()
      await userEvent.click(approveOne('na-', 'wet'))
      await screen.findByText('✓ Approved')

      expect(approveOne('przeczy-', 'tać')).toHaveAttribute('data-start', '4')
      expect(approveOne('do-', 'mu')).toHaveAttribute('data-start', '23')
    })

    it('shows the reason when the backend refuses, and the place can be tried again', async () => {
      await openReview({ putHyphens: () => new Error('The page has changed') })

      await userEvent.click(approveOne('przeczy-', 'tać'))

      expect(await screen.findByText('The page has changed')).toBeInTheDocument()
      expect(approveOne('przeczy-', 'tać')).toBeEnabled()
    })
  })

  describe('approve all', () => {
    it('sends the places that are included in one request', async () => {
      await openReview()
      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))

      await userEvent.click(approveAll())

      await waitFor(() => expect(postCalls()).toHaveLength(1))
      expect(postCalls()[0].body).toEqual({
        fixes: [
          { start: 4, end: 11, before: 'przeczy', after: 'przeczy-' },
          { start: 23, end: 25, before: 'do', after: 'do-' },
          { start: 40, end: 42, before: 'na', after: 'na-' },
        ],
      })
    })

    it('leaves out a place that was taken out', async () => {
      await openReview()
      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))
      await userEvent.click(include('do', 'mu'))

      await userEvent.click(approveAll())

      await waitFor(() => expect(postCalls()).toHaveLength(1))
      expect(
        (postCalls()[0].body as { fixes: { before: string }[] }).fixes.map((f) => f.before),
      ).toEqual(['przeczy', 'na'])
    })

    it('puts all the hyphens in the page and marks the places approved', async () => {
      await openReview()
      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))

      await userEvent.click(approveAll())

      await waitFor(() =>
        expect(pageText().textContent).toBe(
          'Ala przeczy-\ntać kota, i do-\nmu wrócił, a na-\nwet nie',
        ),
      )
      expect(await screen.findAllByText('✓ Approved')).toHaveLength(3)
      expect(approveAll()).toBeDisabled()
    })

    it('moves the places that were not approved by the hyphens that were added', async () => {
      await openReview()

      await userEvent.click(approveAll()) // only przeczy

      await screen.findByText('✓ Approved')
      expect(approveOne('do-', 'mu')).toHaveAttribute('data-start', '24')
      expect(approveOne('na-', 'wet')).toHaveAttribute('data-start', '41')
    })

    it('changes none of the places if the backend refuses, and says why', async () => {
      await openReview({ putHyphens: () => new Error('The page has changed') })
      await userEvent.click(screen.getByRole('button', { name: 'Include all' }))

      await userEvent.click(approveAll())

      expect(await screen.findByRole('alert')).toHaveTextContent('The page has changed')
      expect(screen.queryByText('✓ Approved')).toBeNull()
      expect(approveAll()).toHaveTextContent('Approve all (3)')
      expect(approveAll()).toBeEnabled()
    })
  })

  it('is closed when the user goes back to the editions', async () => {
    await openReview()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))

    await userEvent.click(screen.getByRole('button', { name: 'Back to the editions' }))

    expect(review()).toBeNull()
  })
})
