import { describe, expect, it } from 'vitest'
import { highlightParts, type Entity } from './entities'

const entity = (text: string, start: number, label = 'persName'): Entity => ({
  text,
  label,
  start,
  end: start + [...text].length,
})

describe('highlightParts', () => {
  it('cuts the text into plain and entity parts', () => {
    const text = 'Jan mieszka w Paryżu.'

    expect(highlightParts(text, [entity('Jan', 0), entity('Paryżu', 14)])).toEqual([
      { text: 'Jan', entity: entity('Jan', 0) },
      { text: ' mieszka w ' },
      { text: 'Paryżu', entity: entity('Paryżu', 14) },
      { text: '.' },
    ])
  })

  it('gives the whole text as one plain part when there are no entities', () => {
    expect(highlightParts('Ala ma kota', [])).toEqual([{ text: 'Ala ma kota' }])
  })

  it('gives nothing for an empty text', () => {
    expect(highlightParts('', [])).toEqual([])
  })

  it('uses the offsets, so equal words elsewhere are not marked', () => {
    const parts = highlightParts('Ala i Ala', [entity('Ala', 6)])

    expect(parts.filter((p) => p.entity).map((p) => p.text)).toEqual(['Ala'])
    expect(parts[0]).toEqual({ text: 'Ala i ' })
  })

  it('takes entities in any order', () => {
    const parts = highlightParts('Jan i Ewa', [entity('Ewa', 6), entity('Jan', 0)])

    expect(parts.filter((p) => p.entity).map((p) => p.text)).toEqual(['Jan', 'Ewa'])
  })

  it('handles entities that touch each other and the ends of the text', () => {
    expect(highlightParts('JanEwa', [entity('Jan', 0), entity('Ewa', 3)])).toEqual([
      { text: 'Jan', entity: entity('Jan', 0) },
      { text: 'Ewa', entity: entity('Ewa', 3) },
    ])
  })

  it('converts code point offsets to string positions after astral characters', () => {
    const text = '😀 Jan 😀 Ewa'

    const parts = highlightParts(text, [entity('Jan', 2), entity('Ewa', 8)])

    expect(parts.filter((p) => p.entity).map((p) => p.text)).toEqual(['Jan', 'Ewa'])
    expect(parts.map((p) => p.text).join('')).toBe(text)
  })

  it('handles an entity that contains an astral character', () => {
    const parts = highlightParts('a 😀b c', [entity('😀b', 2)])

    expect(parts.filter((p) => p.entity).map((p) => p.text)).toEqual(['😀b'])
  })

  it.each([
    ['text does not match', [entity('Jan', 1)]],
    ['outside the text', [entity('x', 99)]],
    ['empty', [{ text: '', label: 'x', start: 2, end: 2 }]],
    ['backwards', [{ text: 'a', label: 'x', start: 3, end: 1 }]],
    ['negative start', [{ text: 'Ala', label: 'x', start: -1, end: 2 }]],
  ])('skips an entity that does not fit: %s', (_name, entities) => {
    expect(highlightParts('Ala ma kota', entities)).toEqual([
      { text: 'Ala ma kota' },
    ])
  })

  it('skips an entity that overlaps the one before it', () => {
    const parts = highlightParts('Jan Kowalski', [
      entity('Jan Kowalski', 0),
      entity('Kowalski', 4),
    ])

    expect(parts.filter((p) => p.entity).map((p) => p.text)).toEqual(['Jan Kowalski'])
  })

  it.each([
    ['Ala ma kota', [entity('ma', 4)]],
    ['😀 Jan 😀', [entity('Jan', 2)]],
    ['', []],
    ['x', [entity('y', 0)]],
  ])('never loses or changes text: %s', (text, entities) => {
    expect(
      highlightParts(text, entities)
        .map((part) => part.text)
        .join(''),
    ).toBe(text)
  })
})
