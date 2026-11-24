import { fitRanges, type Entity } from './entities'

/** A word of a text, as the backend's spell check reports it. */
export interface Word {
  text: string
  /** Character offsets, counted in code points (as Python does). */
  start: number
  end: number
  /**
   * The backend's verdict: false if the word is misspelled. The dictionary is a
   * modern one, so a spelling of the edition that was made "legal" (see
   * `addLegalSpelling`) counts as correct here.
   */
  correct: boolean
}

/** A stretch of the text, with the word and the entity it is part of, if any. */
export interface Segment {
  text: string
  word?: Word
  entity?: Entity
}

/**
 * Cut `text` into the stretches that a word, an entity or neither covers. A
 * word and an entity can overlap (an entity of several words, or a word that an
 * entity starts or ends in the middle of), so the cuts are made at the edges of
 * both, and a word may end up in more than one segment. Putting the segments
 * together gives the text again.
 */
export function buildSegments(
  text: string,
  words: Word[],
  entities: Entity[],
): Segment[] {
  const wordRanges = fitRanges(text, words)
  const entityRanges = fitRanges(text, entities)
  const cuts = new Set([0, text.length])
  for (const { from, to } of [...wordRanges, ...entityRanges]) {
    cuts.add(from)
    cuts.add(to)
  }
  const edges = [...cuts].sort((a, b) => a - b)

  const segments: Segment[] = []
  let nextWord = 0
  let nextEntity = 0
  for (let i = 0; i + 1 < edges.length; i++) {
    const from = edges[i]
    const to = edges[i + 1]
    while (nextWord < wordRanges.length && wordRanges[nextWord].to <= from) nextWord++
    while (nextEntity < entityRanges.length && entityRanges[nextEntity].to <= from)
      nextEntity++
    const word = wordRanges[nextWord]
    const entity = entityRanges[nextEntity]
    segments.push({
      text: text.slice(from, to),
      word: word && word.from <= from ? word.item : undefined,
      entity: entity && entity.from <= from ? entity.item : undefined,
    })
  }
  return segments
}

const WORD = /^[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*$/u
const MOST_PARTS = 4

/**
 * The parts of the word that `word` is part of, in order: usually only the word
 * itself, but a word split over lines ("wszechświa-" / "towem": a word, a hyphen at
 * the end of the line, one line break and a word) is two tokens (up to four parts),
 * and either of them gives both. The hyphens of the split are not parts. (Same rule
 * as the backend's, which also looks at the word with the hyphen kept: a word that
 * has its own hyphen is no concern here, as it was typed in the legal spelling by
 * hand.) A token that is no word (a hyphen, a sign) has no parts: the list is empty.
 */
export function partsOf(text: string, words: Word[], word: Word): Word[] {
  const chars = Array.from(text)
  const isPart = (at: number) => at >= 0 && at < words.length && WORD.test(words[at].text)
  // Is words[at] a part that a hyphen and a line break lead to words[at + 2]?
  const splitAfter = (at: number) => {
    if (!isPart(at) || !isPart(at + 2)) return false
    const [part, hyphen, next] = words.slice(at, at + 3)
    return (
      hyphen.text === '-' &&
      part.end === hyphen.start &&
      chars.slice(hyphen.end, next.start).join('') === '\n'
    )
  }
  const index = words.findIndex((w) => w.start === word.start && w.end === word.end)
  if (index < 0 || !isPart(index)) return []
  let first = index
  while (first - 2 >= 0 && index - first < MOST_PARTS && splitAfter(first - 2)) first -= 2
  const parts = [first]
  while (parts.length < MOST_PARTS && splitAfter(parts[parts.length - 1])) {
    parts.push(parts[parts.length - 1] + 2)
  }
  return parts.includes(index) ? parts.map((at) => words[at]) : [words[index]]
}

/**
 * The spelling of the word that `word` is part of: the parts put together, without
 * the hyphens of the split ("wszechświatowem"), whichever part is given; the word
 * itself if it is not split.
 */
export function wholeWord(text: string, words: Word[], word: Word): string {
  const parts = partsOf(text, words, word)
  return parts.length > 0 ? parts.map((part) => part.text).join('') : word.text
}

/** Where the parts of a word that is split over two pages are. */
export interface PageBreak {
  /** The text of the page and its words, and the first line of the next page and its words. */
  text: string
  words: Word[]
  nextText: string
  nextWords: Word[]
}

/**
 * The spelling of a word that is split over two pages: the text of the page ends
 * with the first part and a hyphen ("wszechświa-"), and the first line of the next
 * page starts with the rest ("towem"). `word` is a word of the page (`side` is
 * 'page') or of the first line of the next one ('next'), and `after`, if given, is
 * what it becomes. The result is all the parts put together ("wszechświatowem"),
 * or null if the word is not part of a word that is split over the pages.
 */
export function pageBreakWord(
  { text, words, nextText, nextWords }: PageBreak,
  side: 'page' | 'next',
  word: Word,
  after?: string,
): string | null {
  const content = words
  // the page ends with a word and a hyphen, and nothing else
  const hyphen = content[content.length - 1]
  const last = content[content.length - 2]
  const end = Array.from(text).slice(hyphen?.end ?? 0).join('')
  if (!hyphen || !last || hyphen.text !== '-' || end.trim() !== '' || hyphen.start !== last.end)
    return null
  const first = nextWords[0]
  if (!first || !WORD.test(first.text)) return null
  const before = partsOf(text, words, last)
  const rest = partsOf(nextText, nextWords, first)
  if (before.length === 0 || rest.length === 0) return null
  const mine = side === 'page' ? before : rest
  if (!mine.some((part) => part.start === word.start && part.end === word.end)) return null
  const put = (part: Word, own: Word[]) =>
    own === mine && part.start === word.start && part.end === word.end && after !== undefined
      ? after
      : part.text
  return [...before.map((part) => put(part, before)), ...rest.map((part) => put(part, rest))].join('')
}
