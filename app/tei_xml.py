"""Conversion between page text and TEI XML, using delb."""

import re
from typing import Any, get_args

import delb
from delb import TagNode, tag
from delb.nodes import TextNode

from app.models import EntityLabel

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


def _text_element(root: TagNode) -> TagNode:
    """The <text> of a TEI document that _parse_tei has accepted."""
    return next(
        child
        for child in root.iterate_children()
        if isinstance(child, TagNode)
        and child.universal_name == f"{{{TEI_NAMESPACE}}}text"
    )


# Where a character of the extracted text comes from: its index in a text node
# of the XML, or None for the characters the extraction makes up (the newlines
# for line and paragraph breaks, and the single space left of a run of
# whitespace is taken to come from the first whitespace character of the run).
_Source = tuple[TextNode, int] | None
_Char = tuple[str, _Source]


def _flatten(node: TagNode, out: list[_Char]) -> None:
    """Append the characters under `node`, with marks where blocks and lines end."""
    for child in node.iterate_children():
        if isinstance(child, TextNode):
            out.extend(
                (char, (child, index)) for index, char in enumerate(child.content)
            )
        elif isinstance(child, TagNode):
            name = child.local_name
            if name in _BLOCK_ELEMENTS:
                out.append((_BLOCK_MARK, None))
            _flatten(child, out)
            if name in _BLOCK_ELEMENTS:
                out.append((_BLOCK_MARK, None))
            elif name in _LINE_ELEMENTS:
                out.append((_LINE_MARK, None))


def _normalise_block(block: list[_Char]) -> list[_Char]:
    """Collapse whitespace like XML does, turn line marks into newlines, drop the
    spaces around newlines and at the ends of the block."""
    collapsed: list[_Char] = []
    for char, source in block:
        if char.isspace():
            if collapsed and collapsed[-1][0] == " ":
                continue  # the same run of whitespace
            collapsed.append((" ", source))
        elif char == _LINE_MARK:
            collapsed.append(("\n", None))
        else:
            collapsed.append((char, source))
    result = [
        item
        for index, item in enumerate(collapsed)
        if not (
            item[0] == " "
            and (
                (index > 0 and collapsed[index - 1][0] == "\n")
                or (index + 1 < len(collapsed) and collapsed[index + 1][0] == "\n")
            )
        )
    ]
    while result and result[0][0] in " \n":
        result.pop(0)
    while result and result[-1][0] in " \n":
        result.pop()
    return result


def _extract(text_element: TagNode) -> tuple[str, list[_Source]]:
    """The text of a <text>, and for each of its characters where it comes from."""
    flat: list[_Char] = []
    _flatten(text_element, flat)
    blocks: list[list[_Char]] = [[]]
    for item in flat:
        if item[0] == _BLOCK_MARK:
            blocks.append([])
        else:
            blocks[-1].append(item)

    chars: list[_Char] = []
    for block in (_normalise_block(block) for block in blocks):
        if block:
            if chars:
                chars.extend([("\n", None), ("\n", None)])  # a blank line
            chars.extend(block)
    return "".join(char for char, _ in chars), [source for _, source in chars]


def tei_xml_to_text(xml: str) -> str:
    """Validate a TEI XML document and return the text of its <text>.

    This is the reverse of text_to_tei_xml: blocks (<p>, <head>, <ab>, <div>,
    <lg>) are separated by a blank line, and line breaks (<lb/>, and the end of
    an <l> or <item>) become newlines. Whitespace inside a block is collapsed
    like XML does, so indented XML gives the same text as compact XML. The
    teiHeader and comments are not part of the text.

    Raises InvalidTei if the XML is not a TEI document (see _parse_tei).
    """
    return _extract(_text_element(_parse_tei(xml)))[0]


class TextChanged(ValueError):
    """The text at the given place is not the entity: the page has changed."""


class CannotTag(ValueError):
    """The entity cannot be marked in the XML; the message says why."""


# Elements that cannot be inside a tag around an entity: what an entity can
# span is text, line breaks and inline markup, not paragraphs or lines of verse.
_STRUCTURE = _BLOCK_ELEMENTS | {"l", "item"}


def _text_nodes(node: TagNode) -> list[TextNode]:
    nodes: list[TextNode] = []
    for child in node.iterate_children():
        if isinstance(child, TextNode):
            nodes.append(child)
        elif isinstance(child, TagNode):
            nodes.extend(_text_nodes(child))
    return nodes


def _contains_structure(node: Any) -> bool:
    if not isinstance(node, TagNode):
        return False
    return node.local_name in _STRUCTURE or any(
        _contains_structure(child) for child in node.iterate_children()
    )


def _blank_before(element: TagNode, node: TextNode, index: int) -> bool:
    """Is there only whitespace in `element` before node[index]?"""
    for text_node in _text_nodes(element):
        if text_node is node:
            return not text_node.content[:index].strip()
        if text_node.content.strip():
            return False
    return False


def _blank_after(element: TagNode, node: TextNode, index: int) -> bool:
    """Is there only whitespace in `element` after node[index]?"""
    for text_node in reversed(_text_nodes(element)):
        if text_node is node:
            return not text_node.content[index + 1 :].strip()
        if text_node.content.strip():
            return False
    return False


def _child_of(parent: TagNode, node: Any) -> Any:
    """The child of `parent` that is `node` or contains it."""
    while node.parent is not parent:
        node = node.parent
    return node


def _common_parent(a: Any, b: Any) -> TagNode:
    """The deepest element that contains both nodes."""
    above_b = list(b.iterate_ancestors())
    return next(
        ancestor
        for ancestor in a.iterate_ancestors()
        if any(ancestor is other for other in above_b)
    )


def tag_text_range(xml: str, start: int, end: int, label: str, text: str) -> str:
    """Put the characters `start` to `end` of the text of a TEI document inside
    an element called `label`, and return the changed XML.

    `start` and `end` are character offsets into what tei_xml_to_text returns
    for the same XML, and `text` is what is expected there (the entity): if it
    is not, the text has changed since the offsets were taken. Spaces and line
    breaks at the ends of the range are left outside the new element. The text
    stays the same, so the offsets of other entities remain valid.

    The element goes around whole nodes: a text node is cut where the entity
    starts and ends, and the nodes in between (text, <lb/>, inline markup) move
    into it. So the entity may span line breaks, but it cannot start or end in
    the middle of other markup (e.g. begin inside a <hi> and end outside it),
    and it cannot span paragraphs, verse lines or list items. If the entity is
    already the whole content of an element called `label`, nothing is changed.
    The element it goes into must not contain comments or processing
    instructions, which delb does not expose as children, so they could not be
    moved with the entity.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `text`, and CannotTag if the entity cannot be marked.
    """
    if label not in get_args(EntityLabel):
        raise CannotTag(f"{label!r} is not an entity label")
    root = _parse_tei(xml)
    extracted, sources = _extract(_text_element(root))
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )
    while extracted[start].isspace():
        start += 1
        if start == end:
            raise CannotTag("The entity has no text")
    while extracted[end - 1].isspace():
        end -= 1
    first, last = sources[start], sources[end - 1]
    assert first is not None and last is not None  # not whitespace: real text
    (first_node, first_index), (last_node, last_index) = first, last

    parent = _common_parent(first_node, last_node)
    start_child = _child_of(parent, first_node)
    end_child = _child_of(parent, last_node)
    children = list(parent.iterate_children())
    covered = children[
        next(i for i, c in enumerate(children) if c is start_child) : next(
            i for i, c in enumerate(children) if c is end_child
        )
        + 1
    ]
    if any(_contains_structure(node) for node in covered):
        raise CannotTag("The entity spans more than one paragraph or line")
    if isinstance(start_child, TagNode) and not _blank_before(
        start_child, first_node, first_index
    ):
        raise CannotTag(f"The entity starts inside <{start_child.local_name}>")
    if isinstance(end_child, TagNode) and not _blank_after(
        end_child, last_node, last_index
    ):
        raise CannotTag(f"The entity ends inside <{end_child.local_name}>")
    if parent.local_name == label and all(
        any(node is other for other in covered)
        for node in children
        if not (isinstance(node, TextNode) and not node.content.strip())
    ):
        return xml  # already marked, and the whole of the element
    # Comments and processing instructions are not reachable as children, so
    # they could not be moved along with the entity.
    serialised = str(parent)
    if "<!--" in serialised or "<?" in serialised:
        raise CannotTag(
            f"The entity is in a <{parent.local_name}> that contains a comment or "
            "processing instruction, which cannot be moved"
        )

    # Cut the text nodes at the ends of the entity.
    if isinstance(end_child, TextNode) and last_index + 1 < len(end_child.content):
        tail = end_child.content[last_index + 1 :]
        end_child.content = end_child.content[: last_index + 1]
        end_child.add_following_siblings(tail)
    if isinstance(start_child, TextNode) and first_index > 0:
        entity_part = start_child.content[first_index:]
        start_child.content = start_child.content[:first_index]
        (moved,) = start_child.add_following_siblings(entity_part)
        if end_child is start_child:
            end_child = moved
        covered[0] = moved

    wrapper = TagNode(label, namespace=TEI_NAMESPACE)
    covered[0].add_preceding_siblings(wrapper)
    for node in covered:
        node.detach()
    wrapper.append_children(*covered)

    return str(delb.Document(root)) if xml.lstrip().startswith("<?xml") else str(root)
