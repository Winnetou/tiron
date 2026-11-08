import { splitXml } from './xml'

/** The XML as it is, with the markup on a grey background and in smaller type. */
export default function XmlView({ xml }: { xml: string }) {
  return (
    <pre className="page-box xml-view" role="region" aria-label="Page XML" tabIndex={0}>
      {splitXml(xml).map((part, index) =>
        part.markup ? (
          <span key={index} className="xml-markup">
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </pre>
  )
}
