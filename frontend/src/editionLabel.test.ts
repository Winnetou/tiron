import { describe, expect, it } from 'vitest'
import { authorLabel, editionLabel, titleLabel } from './editionLabel'

describe('authorLabel', () => {
  it('is the last name and the first letter of the first name', () => {
    expect(authorLabel('Kraszewski, Józef Ignacy (1812-1887)')).toBe('Kraszewski J.')
    expect(authorLabel('Kurzeniecki, Ignacy')).toBe('Kurzeniecki I.')
  })

  it('takes the first letter of a name that starts with a letter of its own', () => {
    expect(authorLabel("L'Héritier, Louis François")).toBe("L'Héritier L.")
    expect(authorLabel('Żeromski, Ścibor')).toBe('Żeromski Ś.')
  })

  it('is NN when it is not known', () => {
    expect(authorLabel(null)).toBe('NN')
    expect(authorLabel('')).toBe('NN')
    expect(authorLabel('   ')).toBe('NN')
    expect(authorLabel('(1812-1887)')).toBe('NN')
  })

  it('is the last name alone when there is no first name', () => {
    expect(authorLabel('Prus, (1847-1912)')).toBe('Prus')
  })

  it('leaves a name without a comma as it is, without its years', () => {
    expect(authorLabel('Anonim (XVI w.)')).toBe('Anonim')
    expect(authorLabel('Towarzystwo Naukowe Krakowskie')).toBe(
      'Towarzystwo Naukowe Krakowskie',
    )
  })
})

describe('titleLabel', () => {
  it('keeps a title of up to five words', () => {
    expect(titleLabel('Lalka')).toBe('Lalka')
    expect(titleLabel('Stara baśń')).toBe('Stara baśń')
    expect(titleLabel('Zasady geometryi dla szkół średnich')).toBe(
      'Zasady geometryi dla szkół średnich',
    )
  })

  it('cuts a longer one to its first five words and adds three dots', () => {
    expect(titleLabel('Zasady geometryi dla szkół średnich i wyższych')).toBe(
      'Zasady geometryi dla szkół średnich...',
    )
  })

  it('does not leave a comma or a colon before the dots', () => {
    expect(titleLabel('Mowa pana Kurzenieckiego chorążego powiatu, pińskiego')).toBe(
      'Mowa pana Kurzenieckiego chorążego powiatu...',
    )
    expect(titleLabel('Mowa pana Kurzenieckiego chorążego powiatu: pińskiego')).toBe(
      'Mowa pana Kurzenieckiego chorążego powiatu...',
    )
  })

  it('counts words, whatever the white space between them', () => {
    expect(titleLabel('  Zasady \n geometryi   dla   szkół  średnich  i  wyższych ')).toBe(
      'Zasady geometryi dla szkół średnich...',
    )
  })

  it('is "Bez tytułu" when it is not known', () => {
    expect(titleLabel(null)).toBe('Bez tytułu')
    expect(titleLabel('  ')).toBe('Bez tytułu')
  })
})

describe('editionLabel', () => {
  const id = '00000000-0000-4000-8000-000000000001'

  it('is "Author; title"', () => {
    expect(
      editionLabel({ id, author: 'Kraszewski, Józef Ignacy (1812-1887)', title: 'Serce i ręka' }),
    ).toBe('Kraszewski J.; Serce i ręka')
  })

  it('has NN for an author that is not known, if the title is', () => {
    expect(
      editionLabel({ id, author: null, title: 'Kraków i jego okolice w dawnych czasach' }),
    ).toBe('NN; Kraków i jego okolice w...')
  })

  it('has "Bez tytułu" for a title that is not known, if the author is', () => {
    expect(editionLabel({ id, author: 'Hardy, Thomas (1840-1928)', title: null })).toBe(
      'Hardy T.; Bez tytułu',
    )
  })

  it('is the id when nothing is known of the book', () => {
    expect(editionLabel({ id, author: null, title: null })).toBe(id)
  })
})
