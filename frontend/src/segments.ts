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
 * The spelling of the word that `word` is part of. Usually it is the word itself,
 * but a word split over lines ("wszechświa-" / "towem": a word, a hyphen at the
 * end of the line, one line break and a word) is two tokens, and its spelling is
 * the parts put together, without the hyphen of the split ("wszechświatowem"),
 * whichever part is given. (Same rule as the backend's, which also looks at the
 * word with the hyphen kept: a word that has its own hyphen is no concern here, as
 * its hyphen was typed in the legal spelling by hand.)
 */
export function wholeWord(text: string, words: Word[], word: Word): string {
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
  if (index < 0) return word.text
  let first = index
  // The clicked token may be the hyphen: it has no spelling of its own.
  if (!isPart(first)) return word.text
  while (first - 2 >= 0 && index - first < MOST_PARTS && splitAfter(first - 2)) first -= 2
  const parts = [first]
  while (parts.length < MOST_PARTS && splitAfter(parts[parts.length - 1])) {
    parts.push(parts[parts.length - 1] + 2)
  }
  if (parts.length < 2 || !parts.includes(index)) return word.text
  return parts.map((at) => words[at].text).join('')
}
