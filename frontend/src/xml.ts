// Matches the markup of an XML document: comments, processing instructions and
// the XML declaration, CDATA delimiters, and tags (a `>` inside a quoted
// attribute value does not end the tag). Everything in between is content.
const MARKUP =
  /(<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[|\]\]>|<(?:[^>"']|"[^"]*"|'[^']*')*>)/

export interface XmlPart {
  text: string
  markup: boolean
}

/**
 * Split XML into markup and content, for display only. This is not parsing and
 * checks nothing: whether the XML is correct is up to the backend.
 */
export function splitXml(xml: string): XmlPart[] {
  return xml
    .split(MARKUP)
    .map((text, index) => ({ text, markup: index % 2 === 1 }))
    .filter((part) => part.text !== '')
}
