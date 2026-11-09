import { fitRanges, type Entity } from './entities'

/** A word of a text, as the backend's spell check reports it. */
export interface Word {
  text: string
  /** Character offsets, counted in code points (as Python does). */
  start: number
  end: number
  /** False if the word is misspelled. */
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
