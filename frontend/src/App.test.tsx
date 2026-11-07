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
    const text = screen.getByText(/Ala ma kota/)
    expect(text).toHaveTextContent('Ala ma kota kota ma Ala')
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
    expect(screen.getByText(/Ala ma kota/)).toBeInTheDocument()
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
      await screen.findByText(`text of page ${number}`)
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

      expect(await screen.findByText('text of page 1')).toBeInTheDocument()
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

      expect(await screen.findByText('text of page 1')).toBeInTheDocument()
      expect(fetchMock).toHaveBeenLastCalledWith(`/editions/${UUID}/pages/1`)
      expect(window.location.search).toBe('?page=1')
    })

    it('stays on the page and shows an error when there is no next page', async () => {
      await openPage(3)

      await userEvent.click(screen.getByRole('button', { name: 'Next page' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Page not found',
      )
      expect(screen.getByText('text of page 3')).toBeInTheDocument()
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
})
