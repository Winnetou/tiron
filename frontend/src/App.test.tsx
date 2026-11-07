import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const UUID = '4a2cfdf3-006a-4613-9aee-42b300be604b'

const PAGE = {
  id: 'a8ae3c6d-c4cf-4d11-82d0-112582bb355f',
  edition_id: UUID,
  number: 0,
  content: 'Ala ma kota\nkota ma Ala',
  scan_uri: 'https://polona.pl/iiif/3/x/full/max/0/default.jpg',
  ready: false,
}

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => PAGE,
    ...response,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
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
    const fetchMock = mockFetch({})
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), ` ${UUID} `)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(`/editions/${UUID}/pages/0`)
  })

  it('uses the ?page= URL parameter as the page number', async () => {
    window.history.pushState({}, '', '/?page=99')
    const fetchMock = mockFetch({})
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(`/editions/${UUID}/pages/99`),
    )
    window.history.pushState({}, '', '/')
  })

  it('shows the scan on the left and the raw text on the right', async () => {
    mockFetch({})
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    const image = await screen.findByRole('img')
    expect(image).toHaveAttribute('src', PAGE.scan_uri)
    const text = screen.getByRole('textbox', { name: 'Page text' })
    expect(text).toHaveValue('Ala ma kota\nkota ma Ala')
    // two halves: image first (left), text second (right)
    const [left, right] = Array.from(
      document.querySelectorAll('.viewer-half'),
    )
    expect(left).toContainElement(image)
    expect(right).toContainElement(text)
    expect(screen.queryByLabelText('Edition UUID')).not.toBeInTheDocument()
  })

  it('has a right sidebar with three tool buttons', async () => {
    mockFetch({})
    render(<App />)
    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
    await screen.findByRole('img')

    const sidebar = screen.getByRole('complementary', { name: 'Tools' })
    const buttons = within(sidebar).getAllByRole('button')

    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Structure',
      'Correction',
      'Named entity recognition',
    ])
    // hover text and an icon on each button
    for (const button of buttons) {
      expect(button).toHaveAttribute(
        'data-tooltip',
        button.getAttribute('aria-label'),
      )
      expect(button.querySelector('svg')).not.toBeNull()
    }
    // it is the last column, to the right of the two halves
    expect(sidebar.previousElementSibling).toBe(
      document.querySelectorAll('.viewer-half')[1],
    )
    // stubs: clicking does nothing
    await userEvent.click(buttons[0])
    expect(screen.getByLabelText('Page text')).toHaveValue(PAGE.content)
  })

  it('zooms the scan in and out within limits', async () => {
    mockFetch({})
    render(<App />)
    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))
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

  describe('page navigation', () => {
    async function openPage(number: number) {
      window.history.pushState({}, '', `/?page=${number}`)
      const fetchMock = vi.fn(async (url: string) => {
        const n = Number(url.split('/').pop())
        if (n < 0 || n > 3) {
          return {
            ok: false,
            status: 404,
            statusText: 'Not Found',
            json: async () => ({ detail: 'Page not found' }),
          }
        }
        return {
          ok: true,
          status: 200,
          statusText: 'OK',
          json: async () => ({
            ...PAGE,
            number: n,
            content: `text of page ${n}`,
            scan_uri: `https://polona.pl/scan/${n}.jpg`,
          }),
        }
      })
      vi.stubGlobal('fetch', fetchMock)
      render(<App />)
      await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
      await userEvent.click(screen.getByRole('button', { name: 'Open' }))
      await screen.findByDisplayValue(`text of page ${number}`)
      return fetchMock
    }

    afterEach(() => window.history.pushState({}, '', '/'))

    it('disables previous page on page 0', async () => {
      await openPage(0)

      expect(
        screen.getByRole('button', { name: 'Previous page' }),
      ).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
    })

    it('goes to the next page and updates the URL', async () => {
      const fetchMock = await openPage(0)

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      expect(await screen.findByDisplayValue('text of page 1')).toBeInTheDocument()
      expect(fetchMock).toHaveBeenLastCalledWith(`/editions/${UUID}/pages/1`)
      expect(screen.getByRole('img')).toHaveAttribute(
        'src',
        'https://polona.pl/scan/1.jpg',
      )
      expect(window.location.search).toBe('?page=1')
      expect(
        screen.getByRole('button', { name: 'Previous page' }),
      ).toBeEnabled()
    })

    it('goes to the previous page', async () => {
      const fetchMock = await openPage(2)

      await userEvent.click(
        screen.getByRole('button', { name: 'Previous page' }),
      )

      expect(await screen.findByDisplayValue('text of page 1')).toBeInTheDocument()
      expect(fetchMock).toHaveBeenLastCalledWith(`/editions/${UUID}/pages/1`)
      expect(window.location.search).toBe('?page=1')
    })

    it('stays on the page and shows an error when there is no next page', async () => {
      await openPage(3)

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Page not found',
      )
      expect(screen.getByDisplayValue('text of page 3')).toBeInTheDocument()
      expect(window.location.search).toBe('?page=3')
    })
  })

  it('shows the backend error detail', async () => {
    mockFetch({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: 'Edition not found' }),
    })
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Edition not found',
    )
  })

  it('shows an error when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network down')))
    render(<App />)

    await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Network down')
  })

  describe('editing', () => {
    type Handler = (url: string, init?: RequestInit) => unknown
    let calls: { url: string; init?: RequestInit }[]

    async function openEditable(page: Partial<typeof PAGE> = {}, patch?: Handler) {
      calls = []
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          calls.push({ url, init })
          if (init?.method === 'PATCH') {
            const result = patch
              ? patch(url, init)
              : serverAfterPatch(page, JSON.parse(String(init.body)))
            if (result instanceof Error) {
              return {
                ok: false,
                status: 500,
                statusText: 'Server Error',
                json: async () => ({ detail: result.message }),
              }
            }
            return { ok: true, status: 200, statusText: 'OK', json: async () => result }
          }
          return {
            ok: true,
            status: 200,
            statusText: 'OK',
            json: async () => ({ ...PAGE, ...page }),
          }
        }),
      )
      render(<App />)
      await userEvent.type(screen.getByLabelText('Edition UUID'), UUID)
      await userEvent.click(screen.getByRole('button', { name: 'Open' }))
      return screen.findByRole('textbox', { name: 'Page text' })
    }

    /** What the backend would answer: content is the correction, or the raw text. */
    function serverAfterPatch(
      page: Partial<typeof PAGE>,
      changes: { corrected_content?: string | null; ready?: boolean },
    ) {
      const result = { ...PAGE, ...page }
      if ('ready' in changes) result.ready = changes.ready as boolean
      if ('corrected_content' in changes) {
        result.content = changes.corrected_content || 'raw text'
      }
      return result
    }

    const patchCalls = () => calls.filter((c) => c.init?.method === 'PATCH')

    it('has Save and Mark as ready buttons; Save waits for an edit', async () => {
      await openEditable()

      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(
        screen.getByRole('button', { name: 'Mark as ready' }),
      ).toBeEnabled()
    })

    it('saves the edited text as the corrected content', async () => {
      const textbox = await openEditable()

      await userEvent.clear(textbox)
      await userEvent.type(textbox, 'Ala ma kota.')
      expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Save' }))

      await waitFor(() => expect(patchCalls()).toHaveLength(1))
      const [{ url, init }] = patchCalls()
      expect(url).toBe(`/editions/${UUID}/pages/0`)
      expect(JSON.parse(String(init?.body))).toEqual({
        corrected_content: 'Ala ma kota.',
      })
      await waitFor(() =>
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument(),
      )
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(textbox).toHaveValue('Ala ma kota.')
    })

    it('marks the page as ready, sending only the flag', async () => {
      await openEditable()

      await userEvent.click(
        screen.getByRole('button', { name: 'Mark as ready' }),
      )

      const readyButton = await screen.findByRole('button', { name: 'Ready' })
      expect(readyButton).toBeDisabled()
      expect(patchCalls()).toHaveLength(1)
      expect(JSON.parse(String(patchCalls()[0].init?.body))).toEqual({
        ready: true,
      })
    })

    it('shows an already ready page as Ready', async () => {
      await openEditable({ ready: true })

      expect(screen.getByRole('button', { name: 'Ready' })).toBeDisabled()
    })

    it('keeps the text on the page after marking it ready', async () => {
      const textbox = await openEditable()
      await userEvent.type(textbox, '!')

      await userEvent.click(
        screen.getByRole('button', { name: 'Mark as ready' }),
      )
      await screen.findByRole('button', { name: 'Ready' })

      // the unsaved edit was not sent, and is not thrown away either
      expect(textbox).toHaveValue(`${PAGE.content}!`)
      expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    })

    it('blocks page navigation while there are unsaved changes', async () => {
      const textbox = await openEditable()
      await userEvent.type(textbox, '!')

      expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
      expect(
        screen.getByRole('button', { name: 'Previous page' }),
      ).toBeDisabled()
    })

    it('shows an error and keeps the edit when saving fails', async () => {
      const textbox = await openEditable({}, () => new Error('Page not found'))
      await userEvent.type(textbox, '!')

      await userEvent.click(screen.getByRole('button', { name: 'Save' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Page not found',
      )
      expect(textbox).toHaveValue(`${PAGE.content}!`)
      expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    })

    it('shows an error when marking as ready fails', async () => {
      await openEditable({}, () => new Error('Edition not found'))

      await userEvent.click(
        screen.getByRole('button', { name: 'Mark as ready' }),
      )

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Edition not found',
      )
      expect(screen.getByRole('button', { name: 'Mark as ready' })).toBeEnabled()
    })

    it('shows the server version after saving (e.g. an emptied text falls back to raw)', async () => {
      const textbox = await openEditable({}, () => ({
        ...PAGE,
        content: 'raw text again',
      }))
      await userEvent.clear(textbox)

      await userEvent.click(screen.getByRole('button', { name: 'Save' }))

      await waitFor(() => expect(textbox).toHaveValue('raw text again'))
    })
  })
})
