import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Page } from './api'
import App from './App'
import type { Entity } from './entities'
import './index.css'

const UUID = '4a2cfdf3-006a-4613-9aee-42b300be604b'
const SCAN = 'https://polona.pl/iiif/3/x/full/max/0/default.jpg'

/** The page content is TEI XML, as the backend returns it. */
const xmlOf = (text: string) =>
  `<?xml version="1.0"?><TEI><teiHeader>HEADER</teiHeader><text><p>${text}</p></text></TEI>`
/** What the backend's POST /tei/text answers for such XML. */
const textOf = (xml: string) => xml.match(/<p>(.*)<\/p>/)![1]

const PAGE: Page = {
  id: 'a8ae3c6d-c4cf-4d11-82d0-112582bb355f',
  edition_id: UUID,
  number: 0,
  scan_uri: SCAN,
  ready: false,
  content: xmlOf('Ala ma kota'),
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
    const number = Number(url.split('/').pop())
    const page = pageFor(number)
    if (page instanceof Error) return failure(404, page)
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
    const mark = pageText().querySelector('mark')!
    expect(mark).toHaveAttribute('title', 'persName')
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
    const mark = pageText().querySelector('mark')!
    expect(mark.previousSibling?.textContent).toBe('Ala ma kota, a ')
    expect(mark.nextSibling?.textContent).toBe(' nie')
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
