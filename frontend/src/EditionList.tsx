import { useEffect, useRef, useState } from 'react'
import {
  getEditions,
  searchEditions,
  pageUrl,
  type EditionSearch,
  type EditionsPage,
  DEFAULT_PAGE_NUMBER,
} from './api'
import { editionLabel } from './editionLabel'
import { Loading, PageLoading } from './Loading'
import SearchIcon from './SearchIcon'

/** The page of the list that the address names (?page=, from 1), or the first. */
function listPageFromAddress(): number {
  const value = new URLSearchParams(window.location.search).get('page')
  return value !== null && /^[1-9]\d*$/.test(value) ? Number(value) : 1
}

/** The search that the address names (?author= and/or ?title=), or none. */
function searchFromAddress(): EditionSearch | null {
  const query = new URLSearchParams(window.location.search)
  const search = {
    author: (query.get('author') ?? '').trim(),
    title: (query.get('title') ?? '').trim(),
  }
  return search.author || search.title ? search : null
}

/** The address of a page of the list, or of the search, so that a reload shows it. */
function addressOf(search: EditionSearch | null, page: number): string {
  const query = new URLSearchParams()
  if (search?.author) query.set('author', search.author)
  if (search?.title) query.set('title', search.title)
  if (page !== 1) query.set('page', String(page))
  const text = query.toString()
  return text ? `/?${text}` : '/'
}

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; list: EditionsPage }

/**
 * The main page: the editions of the collection as links, 100 on a page, with
 * Previous and Next to move between the pages of the list. Each link is the
 * address of the first page of its edition.
 *
 * A small search button is in the top right corner, above the title. It shows (and
 * hides) a box with two fields, author and title (rounded, with the name as the
 * placeholder, like the other boxes to write in; round buttons with the yellowish
 * effect, from `review-styled`); "Search" is enabled when either
 * has something in it. The editions that are found take the place of the list, in
 * the same form and with the same Previous and Next, and "Clear search" brings the
 * whole list back. The search is in the address (?author=&title=&page=), as the page
 * of the list is.
 */
export default function EditionList() {
  const [number, setNumber] = useState(listPageFromAddress)
  const [search, setSearch] = useState<EditionSearch | null>(searchFromAddress)
  const [state, setState] = useState<State>({ kind: 'loading' })
  // The box of the search: shown or not, and what is typed in its fields (which is
  // not the search before "Search" is clicked)
  const [boxShown, setBoxShown] = useState(search !== null)
  const [author, setAuthor] = useState(search?.author ?? '')
  const [title, setTitle] = useState(search?.title ?? '')
  const authorField = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let current = true
    ;(search ? searchEditions(search, number) : getEditions(number))
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
  }, [number, search])

  useEffect(() => {
    if (boxShown) authorField.current?.focus()
  }, [boxShown])

  function show(next: number) {
    window.history.replaceState(null, '', addressOf(search, next))
    setState({ kind: 'loading' })
    setNumber(next)
  }

  const canSearch = author.trim() !== '' || title.trim() !== ''

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!canSearch) return
    const found = { author: author.trim(), title: title.trim() }
    window.history.replaceState(null, '', addressOf(found, 1))
    setState({ kind: 'loading' })
    setNumber(1)
    setSearch(found)
  }

  function clear() {
    setAuthor('')
    setTitle('')
    window.history.replaceState(null, '', addressOf(null, 1))
    setState({ kind: 'loading' })
    setNumber(1)
    setSearch(null)
  }

  // While the editions of a search come, or if they cannot be had, the search box
  // stays, to try again or to clear the search; the plain list has the whole page.
  if (!search && state.kind === 'error') {
    return <p role="alert">{state.message}</p>
  }
  if (!search && state.kind === 'loading') {
    return <PageLoading>Loading the editions …</PageLoading>
  }

  const list = state.kind === 'done' ? state.list : null
  const items = list?.items ?? []
  const pages = list?.pages
  return (
    <nav className="editions" aria-label="Editions">
      <div className="editions-tools">
        <button
          type="button"
          className="search-toggle"
          aria-label="Search the editions"
          aria-expanded={boxShown}
          aria-controls="editions-search"
          title="Search by author and title"
          onClick={() => setBoxShown(!boxShown)}
        >
          <SearchIcon />
        </button>
      </div>
      {boxShown && (
        <form
          id="editions-search"
          className="editions-search review-styled"
          role="search"
          aria-label="Search the editions"
          onSubmit={submit}
        >
          <input
            ref={authorField}
            type="text"
            className="styled-input"
            placeholder="Author"
            aria-label="Author"
            value={author}
            maxLength={200}
            autoComplete="off"
            onChange={(event) => setAuthor(event.target.value)}
          />
          <input
            type="text"
            className="styled-input"
            placeholder="Title"
            aria-label="Title"
            value={title}
            maxLength={200}
            autoComplete="off"
            onChange={(event) => setTitle(event.target.value)}
          />
          <div className="editions-search-buttons">
            {search && (
              <button type="button" onClick={clear}>
                Clear search
              </button>
            )}
            <button type="submit" disabled={!canSearch}>
              Search
            </button>
          </div>
        </form>
      )}
      <h1>Editions</h1>
      {search && state.kind === 'loading' && <Loading>Searching …</Loading>}
      {search && state.kind === 'error' && <p role="alert">{state.message}</p>}
      {search && list && (
        <p className="editions-found" role="status">
          {list.total === 0
            ? 'No edition found.'
            : `${list.total} ${list.total === 1 ? 'edition' : 'editions'} found.`}
        </p>
      )}
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <a href={pageUrl(item.id, DEFAULT_PAGE_NUMBER)}>{editionLabel(item)}</a>
          </li>
        ))}
      </ul>
      <div className="editions-pages review-styled">
        <button
          type="button"
          aria-label="Previous"
          disabled={number <= 1}
          onClick={() => show(number - 1)}
        >
          &laquo;
        </button>
        <span>
          Page {number} of {Math.max(pages ?? 1, 1)}
        </span>
        <button
          type="button"
          aria-label="Next"
          disabled={number >= (pages ?? 1)}
          onClick={() => show(number + 1)}
        >
          &raquo;
        </button>
      </div>
      <p className="art-credit">
        Albrecht Dürer, <em>The Four Horsemen</em>, 1498, woodcut (detail). National Gallery
        of Art, Washington. CC0.
      </p>
    </nav>
  )
}
