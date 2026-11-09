import { describe, expect, it } from 'vitest'
import type { Entity } from './entities'
import { buildSegments, type Word } from './segments'

const word = (text: string, start: number, correct = true): Word => ({
  text,
  start,
  end: start + [...text].length,
  correct,
})
const entity = (text: string, start: number, label = 'persName'): Entity => ({
  text,
  label,
  start,
  end: start + [...text].length,
})
const joined = (text: string, words: Word[], entities: Entity[]) =>
  buildSegments(text, words, entities)
    .map((segment) => segment.text)
    .join('')

describe('buildSegments', () => {
  it('is the whole text as one plain segment when there is nothing to mark', () => {
    expect(buildSegments('Ala ma kota', [], [])).toEqual([{ text: 'Ala ma kota' }])
  })

  it('has nothing for an empty text', () => {
    expect(buildSegments('', [], [])).toEqual([])
  })

  it('cuts the text into words and what is between them', () => {
    const w = [word('Ala', 0), word('kota', 7)]

    expect(buildSegments('Ala ma kota.', w, [])).toEqual([
      { text: 'Ala', word: w[0] },
      { text: ' ma ' },
      { text: 'kota', word: w[1] },
      { text: '.' },
    ])
  })

  it('cuts the text into entities and what is between them', () => {
    const e = [entity('Jan', 0)]

    expect(buildSegments('Jan mieszka', [], e)).toEqual([
      { text: 'Jan', entity: e[0] },
      { text: ' mieszka' },
    ])
  })

  it('gives a word inside an entity both', () => {
    const w = [word('Jan', 0), word('Kowalski', 4)]
    const e = [entity('Jan Kowalski', 0)]

    expect(buildSegments('Jan Kowalski mieszka', w, e)).toEqual([
      { text: 'Jan', word: w[0], entity: e[0] },
      { text: ' ', entity: e[0] },
      { text: 'Kowalski', word: w[1], entity: e[0] },
      { text: ' mieszka' },
    ])
  })

  it('cuts a word in two where an entity starts or ends inside it', () => {
    // the entity is "Kowal", the word is "Kowalski"
    const w = [word('Kowalski', 0)]
    const e = [entity('Kowal', 0)]

    const segments = buildSegments('Kowalski', w, e)

    expect(segments).toEqual([
      { text: 'Kowal', word: w[0], entity: e[0] },
      { text: 'ski', word: w[0] },
    ])
    expect(segments[0].word).toBe(segments[1].word) // the same word, in two pieces
  })

  it('handles an entity that ends inside a word and another that starts in it', () => {
    const w = [word('abcdef', 0)]
    const e = [entity('abc', 0), entity('def', 3)]

    expect(buildSegments('abcdef', w, e).map((s) => [s.text, !!s.entity])).toEqual([
      ['abc', true],
      ['def', true],
    ])
  })

  it('handles an entity that begins before a word and ends in the middle of it', () => {
    const w = [word('Kowalski', 4)]
    const e = [entity('Jan Kowal', 0)]

    expect(buildSegments('Jan Kowalski', w, e)).toEqual([
      { text: 'Jan ', entity: e[0] },
      { text: 'Kowal', word: w[0], entity: e[0] },
      { text: 'ski', word: w[0] },
    ])
  })

  it('counts offsets in code points, as the backend does, after emoji', () => {
    const text = '😀 Ala 😀 ma'
    const w = [word('Ala', 2), word('ma', 8)]

    const segments = buildSegments(text, w, [])

    expect(segments.filter((s) => s.word).map((s) => s.text)).toEqual(['Ala', 'ma'])
    expect(joined(text, w, [])).toBe(text)
  })

  it('skips a word that does not fit the text instead of guessing', () => {
    const w = [word('Jan', 0), word('x', 500), word('Ala', 1)]

    const segments = buildSegments('Ala ma', w, [])

    expect(segments.filter((s) => s.word)).toEqual([])
    expect(segments).toEqual([{ text: 'Ala ma' }])
  })

  it('takes words and entities in any order', () => {
    const w = [word('ma', 4), word('Ala', 0)]
    const e = [entity('kota', 7), entity('Ala', 0)]

    const segments = buildSegments('Ala ma kota', w, e)

    expect(segments.filter((s) => s.word).map((s) => s.text)).toEqual(['Ala', 'ma'])
    expect(segments.filter((s) => s.entity).map((s) => s.text)).toEqual(['Ala', 'kota'])
  })

  it.each([
    ['Ala ma kota', [word('Ala', 0), word('kota', 7)], [entity('Ala ma', 0)]],
    ['😀 Jan 😀', [word('Jan', 2)], [entity('Jan', 2)]],
    ['a b', [word('a', 0), word('b', 2)], []],
    ['', [], []],
    ['x', [word('y', 0)], [entity('z', 0)]],
  ])('never loses or changes text: %s', (text, words, entities) => {
    expect(joined(text, words, entities)).toBe(text)
  })
})
