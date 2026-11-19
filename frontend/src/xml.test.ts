import { describe, expect, it } from 'vitest'
import { splitXml } from './xml'

const markup = (xml: string) =>
  splitXml(xml)
    .filter((part) => part.markup)
    .map((part) => part.text)
const content = (xml: string) =>
  splitXml(xml)
    .filter((part) => !part.markup)
    .map((part) => part.text)

describe('splitXml', () => {
  it('separates tags from content', () => {
    expect(splitXml('<p>Ala ma <hi>kota</hi></p>')).toEqual([
      { text: '<p>', markup: true },
      { text: 'Ala ma ', markup: false },
      { text: '<hi>', markup: true },
      { text: 'kota', markup: false },
      { text: '</hi>', markup: true },
      { text: '</p>', markup: true },
    ])
  })

  it('treats the XML declaration, comments and processing instructions as markup', () => {
    const xml = '<?xml version="1.0"?><!-- a <b> comment --><?pi data?><a/>'

    expect(markup(xml)).toEqual([
      '<?xml version="1.0"?>',
      '<!-- a <b> comment -->',
      '<?pi data?>',
      '<a/>',
    ])
    expect(content(xml)).toEqual([])
  })

  it('keeps attributes in the tag, even when a value contains >', () => {
    const xml = `<lb break="no"/><x a="1 > 0" b='<c>'>t</x>`

    expect(markup(xml)).toEqual([
      '<lb break="no"/>',
      `<x a="1 > 0" b='<c>'>`,
      '</x>',
    ])
    expect(content(xml)).toEqual(['t'])
  })

  it('shows CDATA delimiters as markup and its text as content', () => {
    const xml = '<p><![CDATA[a & b]]></p>'

    expect(markup(xml)).toEqual(['<p>', '<![CDATA[', ']]>', '</p>'])
    expect(content(xml)).toEqual(['a & b'])
  })

  it('leaves entities in the content as they are', () => {
    expect(content('<p>a &lt; b &amp; c</p>')).toEqual(['a &lt; b &amp; c'])
  })

  it('keeps line breaks and whitespace of the content', () => {
    expect(content('<p>\n  one\n  two\n</p>')).toEqual(['\n  one\n  two\n'])
  })

  it('has no markup in plain text, and nothing in empty input', () => {
    expect(splitXml('just text')).toEqual([{ text: 'just text', markup: false }])
    expect(splitXml('')).toEqual([])
  })

  it('does not choke on broken XML: it only highlights, the backend judges', () => {
    expect(markup('<p>unclosed <b')).toEqual(['<p>'])
    // an unfinished tag is not recognised as markup, so it stays as content
    expect(content('<p>unclosed <b')).toEqual(['unclosed <b'])
  })

  it.each([
    '<?xml version="1.0"?><TEI><teiHeader/><text><p>a<lb/>b</p></text></TEI>',
    '<a x="1>2">t<!--c--><![CDATA[<z>]]></a>',
    'plain < text > with & stray',
    '',
  ])('loses nothing: the parts add up to the input (%s)', (xml) => {
    expect(
      splitXml(xml)
        .map((part) => part.text)
        .join(''),
    ).toBe(xml)
  })
})
