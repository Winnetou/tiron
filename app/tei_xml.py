"""Conversion between page text and TEI XML, using delb."""

import re
from typing import Any

import delb
from delb import TagNode, tag
from delb.nodes import TextNode

TEI_NAMESPACE = "http://www.tei-c.org/ns/1.0"

# Characters that are not allowed in XML 1.0 (OCR output can contain them).
_ILLEGAL_XML_CHARACTERS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BLANK_LINES = re.compile(r"\n[ \t]*\n")

# Elements of the TEI <text> that form a block of their own (separated from the
# next one by a blank line) and elements that end a line.
_BLOCK_ELEMENTS = {"p", "head", "ab", "div", "lg"}
_LINE_ELEMENTS = {"lb", "l", "item"}  # lb is empty: it breaks the line where it is
# Markers used while extracting text. Both are characters that XML 1.0 does not
# allow, so they cannot occur in the extracted text itself.
_BLOCK_MARK = "\x00"
_LINE_MARK = "\x01"


def _paragraph(block: str) -> Any:
    """A <p> for a block of lines, with an <lb/> between the lines.

    A line that ends with a hyphen continues a word on the next line, which
    is marked with <lb break="no"/>; the hyphen itself is kept in the text.
    """
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    children: list[Any] = []
    for index, line in enumerate(lines):
        if index:
            hyphenated = lines[index - 1].endswith("-")
            children.append(tag("lb", {"break": "no"}) if hyphenated else tag("lb"))
        children.append(line)
    return tag("p", children)


def text_to_tei_xml(text: str) -> delb.Document:
    """Convert the plain text of a page to a TEI XML document.

    The text is split into paragraphs at blank lines (the OCR separates text
    blocks such as the main text and footnotes that way), and the line breaks
    inside a paragraph become <lb/> elements, so the lines of the printed page
    are kept. The document has a minimal teiHeader (a title and a publication
    statement). It has no <sourceDesc>, which the TEI schema requires, so the
    document is well-formed TEI-namespaced XML but does not fully validate
    against the official TEI schemas.
    """
    text = _ILLEGAL_XML_CHARACTERS.sub("", text.replace("\r\n", "\n"))
    blocks = [block for block in _BLANK_LINES.split(text) if block.strip()]
    paragraphs = [_paragraph(block) for block in blocks] or [tag("p")]

    root = TagNode(
        "TEI",
        namespace=TEI_NAMESPACE,
        children=[
            tag(
                "teiHeader",
                tag(
                    "fileDesc",
                    [
                        tag("titleStmt", tag("title", "OCR text of a page")),
                        tag("publicationStmt", tag("p", "Unpublished")),
                    ],
                ),
            ),
            tag("text", tag("body", paragraphs)),
        ],
    )
    return delb.Document(root)


class InvalidTei(ValueError):
    """The given text is not acceptable TEI XML; the message says why."""


def _parse_tei(xml: str) -> TagNode:
    """Parse the XML and check that it is a TEI document. Returns its root.

    Checked: the text is well-formed XML without a DTD, the root element is
    TEI in the TEI namespace, and it has a teiHeader and a text. This is a check
    of the TEI skeleton, not a validation against the full TEI schema.
    """
    if not xml.strip():
        raise InvalidTei("The XML is empty")
    # No DTDs: TEI does not need them, and entity declarations can be abused to
    # make a small document expand into a huge one.
    if re.search(r"<!(DOCTYPE|ENTITY)", xml, re.IGNORECASE):
        raise InvalidTei("DOCTYPE and ENTITY declarations are not allowed")
    try:
        root = delb.parse_tree(xml)
    except Exception as error:  # the parser backends raise their own exceptions
        detail = re.sub(
            r"^<unknown>:(\d+):(\d+): ", r"line \1, column \2: ", str(error)
        )
        raise InvalidTei(f"The text is not well-formed XML ({detail})") from error
    if (
        not isinstance(root, TagNode)
        or root.universal_name != f"{{{TEI_NAMESPACE}}}TEI"
    ):
        raise InvalidTei(
            f"The root element must be <TEI> in the namespace {TEI_NAMESPACE}"
        )
    children = {
        child.universal_name
        for child in root.iterate_children()
        if isinstance(child, TagNode)
    }
    for name in ("teiHeader", "text"):
        if f"{{{TEI_NAMESPACE}}}{name}" not in children:
            raise InvalidTei(f"The <TEI> element has no <{name}>")
    return root


def _collect(node: TagNode, out: list[str]) -> None:
    """Append the text under `node`, with markers where blocks and lines end."""
    for child in node.iterate_children():
        if isinstance(child, TextNode):
            out.append(child.content)
        elif isinstance(child, TagNode):
            name = child.local_name
            if name in _BLOCK_ELEMENTS:
                out.append(_BLOCK_MARK)
            _collect(child, out)
            if name in _BLOCK_ELEMENTS:
                out.append(_BLOCK_MARK)
            elif name in _LINE_ELEMENTS:
                out.append(_LINE_MARK)


def tei_xml_to_text(xml: str) -> str:
    """Validate a TEI XML document and return the text of its <text>.

    This is the reverse of text_to_tei_xml: blocks (<p>, <head>, <ab>, <div>,
    <lg>) are separated by a blank line, and line breaks (<lb/>, and the end of
    an <l> or <item>) become newlines. Whitespace inside a block is collapsed
    like XML does, so indented XML gives the same text as compact XML. The
    teiHeader and comments are not part of the text.

    Raises InvalidTei if the XML is not a TEI document (see _parse_tei).
    """
    root = _parse_tei(xml)
    text_element = next(
        child
        for child in root.iterate_children()
        if isinstance(child, TagNode)
        and child.universal_name == f"{{{TEI_NAMESPACE}}}text"
    )
    parts: list[str] = []
    _collect(text_element, parts)

    paragraphs = []
    for block in "".join(parts).split(_BLOCK_MARK):
        block = re.sub(r"\s+", " ", block)  # collapse source whitespace
        block = block.replace(_LINE_MARK, "\n")
        block = re.sub(r" *\n *", "\n", block).strip()
        if block:
            paragraphs.append(block)
    return "\n\n".join(paragraphs)
