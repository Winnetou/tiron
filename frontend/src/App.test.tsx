import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Page } from './api'
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
  xml.match(/<p>(.*)<\/p>/)![1].replace(/<[^>]+>/g, '') // tags are not text

const PAGE: Page = {
  id: 'a8ae3c6d-c4cf-4d11-82d0-112582bb355f',
  edition_id: UUID,
  number: 0,
  scan_uri: SCAN,
  ready: false,
  content: xmlOf('Ala ma kota'),
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
    const tagging = url.match(/\/pages\/(\d+)\/entities$/)
    const correcting = url.match(/\/pages\/(\d+)\/corrections$/)
    const number = Number(
      tagging?.[1] ?? correcting?.[1] ?? url.split('/').pop(),
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

async function openPage(options?: Options, search = '') {
  window.history.pushState({}, '', `/${search}`)
  const fetchMock = fakeBackend(options)
  render(<App />)
  await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
  await userEvent.click(screen.getByRole('button', { name: 'Open' }))
  return fetchMock
}

const pageText = () => screen.getByRole('region', { name: 'Page text' })
const calledUrls = () => calls.map((c) => `${c.method} ${c.url}`)

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

describe('first screen', () => {
  it('asks for a uuid and nothing else', () => {
    render(<App />)

    expect(screen.getByLabelText('Edition UUID')).toBeInTheDocument()
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled()
  })

  it('keeps the button disabled for an invalid uuid', async () => {
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), 'not-a-uuid')

    expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled()
  })

  it('requests page 0 of the edition on submit', async () => {
    const fetchMock = await openPage()

    await screen.findByRole('region', { name: 'Page text' })
    expect(fetchMock).toHaveBeenCalledWith(`/editions/${UUID}/pages/0`)
  })

  it('uses the ?page= URL parameter as the page number', async () => {
    await openPage({}, '?page=2')

    await waitFor(() => expect(pageText().textContent).toBe('text of page 2'))
    expect(calledUrls()[0]).toBe(`GET /editions/${UUID}/pages/2`)
  })

  it('shows the backend error detail when the page cannot be loaded', async () => {
    await openPage({ page: () => new Error('Edition not found') })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Edition not found',
    )
    expect(calledUrls()).toEqual([`GET /editions/${UUID}/pages/0`]) // no TEI call
  })

  it('shows an error when the request fails', async () => {
    window.history.pushState({}, '', '/')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network down')))
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Network down')
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
    render(<App />)
    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

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

describe('switch between text and XML', () => {
  const textButton = () => screen.getByRole('button', { name: 'Text' })
  const xmlButton = () => screen.getByRole('button', { name: 'XML' })
  const xmlBox = () => screen.getByRole('region', { name: 'Page XML' })

  async function openText() {
    await openPage()
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))
  }

  it('starts on the text, with the switch showing it', async () => {
    await openText()

    expect(textButton()).toHaveAttribute('aria-pressed', 'true')
    expect(xmlButton()).toHaveAttribute('aria-pressed', 'false')
  })

  it('shows the TEI XML content as it is when switched to XML', async () => {
    await openText()

    await userEvent.click(xmlButton())

    expect(xmlBox().textContent).toBe(xmlOf('text of page 0'))
    expect(xmlButton()).toHaveAttribute('aria-pressed', 'true')
    expect(textButton()).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('region', { name: 'Page text' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Page XML' })).not.toBeNull()
  })

  it('switches back to the text', async () => {
    await openText()
    await userEvent.click(xmlButton())

    await userEvent.click(textButton())

    expect(pageText().textContent).toBe('text of page 0')
    expect(screen.queryByRole('region', { name: 'Page XML' })).toBeNull()
  })

  it('makes no request: both views come from the same load', async () => {
    await openText()
    const before = calls.length

    await userEvent.click(xmlButton())
    await userEvent.click(textButton())

    expect(calls).toHaveLength(before)
  })

  it('shows the XML read-only: it is not an input', async () => {
    await openText()

    await userEvent.click(xmlButton())
    await userEvent.type(xmlBox(), 'x')

    expect(screen.queryByRole('textbox', { name: 'Page XML' })).toBeNull()
    expect(xmlBox().textContent).toBe(xmlOf('text of page 0'))
  })

  it('puts the markup in grey, smaller type, and leaves the content alone', async () => {
    await openText()

    await userEvent.click(xmlButton())

    const markup = Array.from(xmlBox().querySelectorAll('.xml-markup')).map(
      (el) => el.textContent,
    )
    expect(markup).toEqual([
      '<?xml version="1.0"?>',
      '<TEI>',
      '<teiHeader>',
      '</teiHeader>',
      '<text>',
      '<p>',
      '</p>',
      '</text>',
      '</TEI>',
    ])
    // the content is what is not markup
    expect(xmlBox().textContent).toContain('HEADER')
    expect(xmlBox().textContent).toContain('text of page 0')
    for (const text of markup) expect(text).toMatch(/^</)
    const spanTexts = Array.from(xmlBox().querySelectorAll('span')).map(
      (el) => el.textContent,
    )
    expect(spanTexts.join('')).not.toContain('HEADER')
    expect(spanTexts.join('')).not.toContain('text of page 0')

    // markup: grey background and a smaller font than the content around it
    const tag = xmlBox().querySelector('.xml-markup') as HTMLElement
    const tagStyle = getComputedStyle(tag)
    const [r, g, b] = tagStyle.backgroundColor.match(/\d+/g)!.map(Number)
    expect(r).toBe(g)
    expect(g).toBe(b)
    expect(r).toBeLessThan(255) // not white
    expect(r).toBeGreaterThan(150) // light grey, so the text stays readable
    const contentSize = parseFloat(getComputedStyle(xmlBox()).fontSize)
    expect(parseFloat(tagStyle.fontSize)).toBeLessThan(contentSize)
  })

  it('keeps the chosen view when moving to another page', async () => {
    await openText()
    await userEvent.click(xmlButton())

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() =>
      expect(xmlBox().textContent).toBe(xmlOf('text of page 1')),
    )
    expect(xmlButton()).toHaveAttribute('aria-pressed', 'true')
  })

  it('still shows the XML when the backend rejects it as TEI', async () => {
    await openPage({ tei: () => new Error('The <TEI> element has no <text>') })
    await screen.findByRole('alert')

    await userEvent.click(xmlButton())

    expect(xmlBox().textContent).toBe(xmlOf('text of page 0'))
    expect(screen.getByRole('alert')).toHaveTextContent('has no <text>')
  })

  it('stays on the XML when the page is marked ready', async () => {
    await openText()
    await userEvent.click(xmlButton())

    await userEvent.click(screen.getByRole('button', { name: 'Mark as ready' }))

    await screen.findByRole('button', { name: 'Ready' })
    expect(xmlBox().textContent).toBe(xmlOf('text of page 0'))
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

  it('highlights in red', async () => {
    await openWithText('Adam Mickiewicz', [entity('Adam Mickiewicz', 0)])

    await userEvent.click(nerButton())

    const mark = await waitFor(() => {
      const found = pageText().querySelector('mark')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    const [r, g, b] = getComputedStyle(mark).color.match(/\d+/g)!.map(Number)
    expect(r).toBeGreaterThan(150)
    expect(g).toBeLessThan(50)
    expect(b).toBeLessThan(50)
    const [br, bg, bb] = getComputedStyle(mark).backgroundColor.match(/\d+/g)!.map(Number)
    expect(br).toBeGreaterThan(bg) // a red-tinted background
    expect(br).toBeGreaterThan(bb)
    // ...and the text around it is not red
    expect(getComputedStyle(pageText()).color).not.toBe(getComputedStyle(mark).color)
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

  it('says how many entities were found', async () => {
    await openWithText('Jan i Ewa', [entity('Jan', 0), entity('Ewa', 6)])

    await userEvent.click(nerButton())

    expect(await screen.findByText('2 entities')).toBeInTheDocument()
  })

  it('says when there are none', async () => {
    await openWithText('Ala ma kota', [])

    await userEvent.click(nerButton())

    expect(await screen.findByText('No entities')).toBeInTheDocument()
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

  it('switches to the text view, where the entities are shown', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])
    await userEvent.click(screen.getByRole('button', { name: 'XML' }))
    expect(screen.queryByRole('region', { name: 'Page text' })).toBeNull()

    await userEvent.click(nerButton())

    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))
    expect(screen.getByRole('button', { name: 'Text' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
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

  it('keeps the highlights when the page is marked ready: same text', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])
    await userEvent.click(nerButton())
    await waitFor(() => expect(marks()).toEqual(['Jan Kowalski']))

    await userEvent.click(screen.getByRole('button', { name: 'Mark as ready' }))

    await screen.findByRole('button', { name: 'Ready' })
    expect(marks()).toEqual(['Jan Kowalski'])
  })

  it('cannot be used when there is no text', async () => {
    await openPage({ tei: () => new Error('The XML is empty') })
    await screen.findByRole('alert')

    expect(nerButton()).toBeDisabled()
    expect(nerCalls()).toHaveLength(0)
  })

  it('leaves the other tool buttons as stubs', async () => {
    await openWithText('Jan Kowalski', [entity('Jan Kowalski', 0)])

    await userEvent.click(screen.getByRole('button', { name: 'Structure' }))
    await userEvent.click(screen.getByRole('button', { name: 'Correction' }))

    expect(nerCalls()).toHaveLength(0)
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
  const tagCalls = () => calls.filter((c) => c.url.endsWith('/entities'))
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

    it('changes the XML: the entity is inside its element', async () => {
      await recognized()
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await waitFor(() => expect(mark('Paryżu')).toHaveClass('confirmed'))
      await userEvent.click(screen.getByRole('button', { name: 'XML' }))
      expect(screen.getByRole('region', { name: 'Page XML' }).textContent).toContain(
        '<placeName>Paryżu</placeName>',
      )
    })

    it('does not touch the XML in the browser: it shows what the backend returns', async () => {
      await recognized({ tag: () => ({ content: xmlOf('what the backend says') }) })
      await hover('Paryżu')

      await userEvent.click(within(dialog()!).getByRole('button', { name: 'Confirm' }))

      await userEvent.click(await screen.findByRole('button', { name: 'XML' }))
      await waitFor(() =>
        expect(screen.getByRole('region', { name: 'Page XML' }).textContent).toBe(
          xmlOf('what the backend says'),
        ),
      )
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
      expect(screen.getByText('3 entities')).toBeInTheDocument()
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

    it('puts that element in the XML', async () => {
      await recognized()

      await confirmWith('Adam Mickiewicz', 'geogName')

      await waitFor(() => expect(mark('Adam Mickiewicz')).toHaveClass('confirmed'))
      await userEvent.click(screen.getByRole('button', { name: 'XML' }))
      const xml = screen.getByRole('region', { name: 'Page XML' }).textContent
      expect(xml).toContain('<geogName>Adam Mickiewicz</geogName>')
      expect(xml).not.toContain('<persName>')
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

    it('counts the entities that are left', async () => {
      await recognized()
      expect(screen.getByText('3 entities')).toBeInTheDocument()

      await reject('Paryżu')

      expect(screen.getByText('2 entities')).toBeInTheDocument()
    })

    it('says there are none when all were rejected', async () => {
      await recognized()

      await reject('Paryżu')
      await reject('Adam Mickiewicz')
      await reject('1840 roku')

      expect(screen.getByText('No entities')).toBeInTheDocument()
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
      // no new recognition: what was found, and decided, for this text is back
      expect(pageText().querySelectorAll('mark')).toHaveLength(3)
      expect(mark('Paryżu')).toHaveClass('confirmed')
      expect(mark('Adam Mickiewicz')).not.toHaveClass('confirmed')
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

  it('counts the misspelled words', async () => {
    await open()

    expect(screen.getByText('2 misspelled')).toBeInTheDocument()
  })

  it('says when all words are correct', async () => {
    await open({ ...spellOptions(), spell: (text) => wordsOf(text, []) })

    expect(screen.getByText('No misspelled words')).toBeInTheDocument()
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
      expect(pageText().textContent).toBe('Ala ma kota, a Legal spelling tey nie ma.')
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

    it('Enter on an empty field sends nothing', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.clear(editor()!)

      await userEvent.keyboard('{Enter}')

      expect(editor()).toBeNull()
      expect(correctionCalls()).toEqual([])
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
      expect(screen.getByText('1 misspelled')).toBeInTheDocument()
    })

    it('shows the new word in the XML, which the backend changed', async () => {
      await open()

      await correct('jey', 'jej')

      await waitFor(() => expect(pageText().textContent).toContain('jej'))
      await userEvent.click(screen.getByRole('button', { name: 'XML' }))
      const xml = screen.getByRole('region', { name: 'Page XML' }).textContent
      expect(xml).toContain('jej')
      expect(xml).not.toContain('jey')
    })

    it('does not touch the XML in the browser: it shows what the backend returns', async () => {
      await open({ correct: () => ({ content: xmlOf('what the backend says') }) })

      await correct('jey', 'jej')

      await waitFor(() =>
        expect(pageText().textContent).toBe('what the backend says'),
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
      expect(screen.getByRole('button', { name: 'Mark as ready' })).toBeEnabled()
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

    it('words inside an entity are words, and the entity is still marked', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])

      const mark = pageText().querySelector('mark.entity')!
      expect(mark.textContent).toBe('Jan Kowalski')
      expect(Array.from(mark.querySelectorAll('.word')).map((w) => w.textContent)).toEqual([
        'Jan',
        'Kowalski',
      ])
      expect(mark.querySelector('.word.misspelled')?.textContent).toBe('Kowalski')
      expect(pageText().textContent).toBe(ETEXT)
    })

    it('a word inside an entity can be corrected', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])

      await userEvent.click(word('Kowalski'))
      await userEvent.keyboard('Kowalsky{Enter}')

      await waitFor(() => expect(correctionCalls()).toHaveLength(1))
      expect(correctionCalls()[0].body).toMatchObject({
        before: 'Kowalski',
        after: 'Kowalsky',
        start: ETEXT.indexOf('Kowalski'),
      })
    })

    it('shows no entity dialog over a word that is being edited', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])
      await userEvent.hover(pageText().querySelector('mark.entity')!)
      expect(screen.queryByRole('dialog')).toBeInTheDocument()

      await userEvent.click(word('Kowalski'))

      expect(editor()).toBeInTheDocument()
      expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('the dialog is back for the entity once the editing is over', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])
      await userEvent.click(word('Kowalski'))
      await userEvent.keyboard('{Escape}')
      await userEvent.unhover(pageText().querySelector('mark.entity')!)

      await userEvent.hover(pageText().querySelector('mark.entity')!)

      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('an entity that starts in the middle of a word cuts the word in two, and either part edits all of it', async () => {
      // the entity is "Kowal", the word "Kowalski"
      await recognized([entity('Kowal', 'persName')].map((e) => ({
        ...e,
        start: ETEXT.indexOf('Kowalski'),
        end: ETEXT.indexOf('Kowalski') + 5,
      })))
      const pieces = [word('Kowal'), word('ski')]
      expect(pieces[0].closest('mark')).not.toBeNull() // the part in the entity
      expect(pieces[1].closest('mark')).toBeNull() // and the part after it

      await userEvent.click(pieces[1])

      expect(editor()).toHaveValue('Kowalski') // the whole word
      // the word is the field, and (it is misspelled) its button
      expect(pageText().textContent).toBe(
        ETEXT.replace('Kowalski', 'Legal spelling'),
      )
    })

    it('after a correction the entities of the old text are not shown for the new one', async () => {
      await recognized([entity('Jan Kowalski', 'persName')])

      await userEvent.click(word('Jan'))
      await userEvent.keyboard('Janek{Enter}')

      await waitFor(() => expect(pageText().textContent).toContain('Janek'))
      expect(pageText().querySelectorAll('mark')).toHaveLength(0)
    })
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
      expect(screen.getByText('3 misspelled')).toBeInTheDocument()
      await userEvent.click(word('jey'))

      await userEvent.click(legalButton()!)

      await waitFor(() => expect(editor()).toBeNull())
      // "jey" and its capital form "Jey" are both legal now
      expect(screen.getByText('1 misspelled')).toBeInTheDocument()
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
      expect(screen.getByRole('button', { name: 'Mark as ready' })).toBeEnabled()
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

    it('sends nothing when the word is not changed, or empty', async () => {
      await open()
      await userEvent.click(word('jey'))
      await userEvent.keyboard('{Enter}')
      await userEvent.click(word('jey'))
      await userEvent.clear(editor()!)
      await userEvent.keyboard('{Enter}')

      expect(correctionCalls()).toEqual([])
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
    const buttons = within(sidebar).getAllByRole('button')

    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Structure',
      'Correction',
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
  const open = (number: number) => openPage({}, `?page=${number}`)

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
    expect(window.location.search).toBe('?page=1')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })

  it('goes to the previous page', async () => {
    await open(2)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 2'))

    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }))

    await waitFor(() => expect(pageText().textContent).toBe('text of page 1'))
    expect(window.location.search).toBe('?page=1')
  })

  it('stays on the page and shows an error when there is no next page', async () => {
    await open(3)
    await waitFor(() => expect(pageText().textContent).toBe('text of page 3'))

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Page not found')
    expect(pageText().textContent).toBe('text of page 3')
    expect(window.location.search).toBe('?page=3')
  })
})

describe('mark as ready', () => {
  it('sends only the flag, then shows the page as Ready', async () => {
    await openPage()
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    await userEvent.click(screen.getByRole('button', { name: 'Mark as ready' }))

    const ready = await screen.findByRole('button', { name: 'Ready' })
    expect(ready).toBeDisabled()
    const patches = calls.filter((c) => c.method === 'PATCH')
    expect(patches).toHaveLength(1)
    expect(patches[0].url).toBe(`/editions/${UUID}/pages/0`)
    expect(patches[0].body).toEqual({ ready: true })
    expect(pageText().textContent).toBe('text of page 0') // the text is still shown
  })

  it('shows an already ready page as Ready', async () => {
    await openPage({ page: () => ({ ready: true }) })

    expect(await screen.findByRole('button', { name: 'Ready' })).toBeDisabled()
  })

  it('shows an error when marking as ready fails', async () => {
    await openPage({ patch: new Error('Page not found') })
    await waitFor(() => expect(pageText().textContent).toBe('text of page 0'))

    await userEvent.click(screen.getByRole('button', { name: 'Mark as ready' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Page not found')
    expect(screen.getByRole('button', { name: 'Mark as ready' })).toBeEnabled()
  })
})
