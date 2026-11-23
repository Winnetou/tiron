import { useEffect, useState } from 'react'
import { getEditions, pageUrl, type EditionsPage, DEFAULT_PAGE_NUMBER } from './api'
import { editionLabel } from './editionLabel'

/** The page of the list that the address names (?page=, from 1), or the first. */
function listPageFromAddress(): number {
  const value = new URLSearchParams(window.location.search).get('page')
  return value !== null && /^[1-9]\d*$/.test(value) ? Number(value) : 1
}

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; list: EditionsPage }

/**
 * The main page: the editions of the collection as links, 100 on a page, with
 * Previous and Next to move between the pages of the list. Each link is the
 * address of the first page of its edition.
 */
export default function EditionList() {
  const [number, setNumber] = useState(listPageFromAddress)
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let current = true
    getEditions(number)
      .then((list) => current && setState({ kind: 'done', list }))
      .catch(
        (error: unknown) =>
          current &&
          setState({
            kind: 'error',
            message: error instanceof Error ? error.message : 'Request failed',
          }),
      )
    return () => {
      current = false
    }
  }, [number])

  function show(next: number) {
    window.history.replaceState(null, '', next === 1 ? '/' : `/?page=${next}`)
    setState({ kind: 'loading' })
    setNumber(next)
  }

  if (state.kind === 'error') return <p role="alert">{state.message}</p>
  if (state.kind === 'loading') return <p role="status">Loading the editions …</p>

  const { items, pages } = state.list
  return (
    <nav className="editions" aria-label="Editions">
      <h1>Editions</h1>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <a href={pageUrl(item.id, DEFAULT_PAGE_NUMBER)}>{editionLabel(item)}</a>
          </li>
        ))}
      </ul>
      <div className="editions-pages review-styled">
        <button type="button" disabled={number <= 1} onClick={() => show(number - 1)}>
          Previous
        </button>
        <span>
          Page {number} of {Math.max(pages ?? 1, 1)}
        </span>
        <button
          type="button"
          disabled={number >= (pages ?? 1)}
          onClick={() => show(number + 1)}
        >
          Next
        </button>
      </div>
      <p className="art-credit">
        Albrecht Dürer, <em>The Four Horsemen</em>, 1498, woodcut (detail). National Gallery
        of Art, Washington. CC0.
      </p>
    </nav>
  )
}
