/** The most words of a title that the link of an edition shows. */
const TITLE_WORDS = 5

/** What the title is when it is not known ("Without a title"). */
const NO_TITLE = 'Bez tytułu'

/**
 * The author as the link shows it: the last name and the first letter of the
 * first name, "Kraszewski J." for "Kraszewski, Józef Ignacy (1812-1887)" (the way
 * the catalogue writes it: last name, a comma, the first names, the years). NN
 * (nomen nescio) if the author is not known. A name without a comma, "Anonim" or a
 * society, has no first name to shorten and is shown as it is, without its years.
 */
export function authorLabel(author: string | null): string {
  const written = (author ?? '').replace(/\([^)]*\)/g, ' ').replace(/\s+/g, ' ').trim()
  if (!written) return 'NN'
  const comma = written.indexOf(',')
  if (comma < 0) return written
  const last = written.slice(0, comma).trim()
  const initial = Array.from(written.slice(comma + 1).trim())[0] ?? ''
  if (!last) return initial ? `${initial}.` : 'NN'
  return initial ? `${last} ${initial}.` : last
}

/**
 * The title as the link shows it: its first five words, and "..." after them if it
 * has more. (The signs that end the fifth word, a comma or a colon, are left out
 * before the dots.) A title that is not known is "Bez tytułu".
 */
export function titleLabel(title: string | null): string {
  const words = (title ?? '').split(/\s+/).filter(Boolean)
  if (words.length === 0) return NO_TITLE
  if (words.length <= TITLE_WORDS) return words.join(' ')
  return `${words.slice(0, TITLE_WORDS).join(' ').replace(/[\s,;:.\-–—]+$/u, '')}...`
}

/**
 * The line above a page: what the link of the edition says (`editionLabel`) and,
 * after a dot between, the place and the year, "Prus B.; Lalka · Warszawa, 1890". A
 * full stop or a comma that the catalogue leaves at the end of the place
 * ("Warszawa.") is dropped. What is not known is left out.
 */
export function editionSummary(edition: {
  id: string
  author: string | null
  title: string | null
  place: string | null
  year: number | null
}): string {
  const place = (edition.place ?? '').replace(/[\s.,;:]+$/u, '').trim()
  const where = [place, edition.year === null ? '' : String(edition.year)]
    .filter(Boolean)
    .join(', ')
  const label = editionLabel(edition)
  return where ? `${label} · ${where}` : label
}

/**
 * What the link of an edition says: "Author; title", the author as `authorLabel`
 * and the title as `titleLabel` ("NN" and "Bez tytułu" for what is not known). If
 * neither the author nor the title is known, the edition has no metadata yet and it
 * is its id.
 */
export function editionLabel(edition: {
  id: string
  author: string | null
  title: string | null
}): string {
  const author = authorLabel(edition.author)
  const title = titleLabel(edition.title)
  if (author === 'NN' && title === NO_TITLE) return edition.id
  return `${author}; ${title}`
}
