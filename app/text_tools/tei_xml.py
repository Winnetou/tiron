"""Conversion between page text and TEI XML, using delb."""

import re
from collections.abc import Callable
from typing import Any, get_args
from xml.sax.saxutils import quoteattr

import delb
from delb import TagNode, tag
from delb.nodes import TextNode

from app.view_models import LANGUAGES, EntityLabel, SectionKind

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
            if (
                node.local_name == "noteGrp"
                and "\n" in child.content
                and not child.content.strip()
            ):
                # A <noteGrp> may not hold an <lb/>, so a line break between its
                # notes is a newline in the XML, which is a line break here.
                out.append((_LINE_MARK, None))
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


def _is_section(node: TagNode) -> bool:
    """Is the element one of those that mark_section makes?"""
    name = node.local_name
    if name == "div":
        return _attributes(node).get("type") == "toc"
    if name == "quote":
        return _LANG in _attributes(node)  # a quotation in another language
    return name in {"fw", "noteGrp", "note"}


def _marked_elements(
    text_element: TagNode, wanted: Callable[[TagNode], bool]
) -> tuple[str, list[tuple[TagNode, int, int]]]:
    """The text of a <text>, and the elements in it that `wanted` picks, in the
    order of the document (outer ones first), each with the characters of the
    text that it covers: `start` to `end`, in code points. An element without
    text is left out."""
    text, sources = _extract(text_element)
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for index, source in enumerate(sources):
        if source is not None:
            key = id(source[0])
            first.setdefault(key, index)
            last[key] = index

    found: list[tuple[TagNode, int, int]] = []

    def walk(node: TagNode) -> None:
        for child in node.iterate_children():
            if not isinstance(child, TagNode):
                continue
            if wanted(child):
                keys = [id(t) for t in _text_nodes(child) if id(t) in first]
                if keys:
                    found.append(
                        (
                            child,
                            min(first[k] for k in keys),
                            max(last[k] for k in keys) + 1,
                        )
                    )
            walk(child)

    walk(text_element)
    return text, found


def tei_xml_sections(xml: str) -> list[dict[str, Any]]:
    """The sections of a page that are marked in the XML (the elements that
    mark_section makes: <fw>, a <div> of type "toc", <noteGrp> and <note>), in
    the order of the document, outer ones first.

    Each is `start` and `end`, the characters of the text that tei_xml_to_text
    gives for the same XML (in code points), and `open` and `close`, the tags as
    written in the XML (`<fw type="header">` and `</fw>`). An element without
    text is left out.

    Raises InvalidTei if the XML is not a TEI document (see _parse_tei).
    """
    _, found = _marked_elements(_text_element(_parse_tei(xml)), _is_section)
    return [
        {
            "start": start,
            "end": end,
            "open": f"<{node.local_name}"
            + "".join(
                f" {_written(name)}={quoteattr(value)}"
                for name, value in _attributes(node).items()
            )
            + ">",
            "close": f"</{node.local_name}>",
        }
        for node, start, end in found
    ]


def tei_xml_entities(xml: str) -> list[dict[str, Any]]:
    """The named entities that are marked in the XML: every element that
    tag_text_range can make (<persName>, <placeName>, <orgName>, <geogName>,
    <date>, <time>, <ref>, <bibl>, <cit>), whoever put it there, in the order of
    the document.

    Each is the `label` (the element's name), its `text`, and `start` and `end`,
    the characters of the text that tei_xml_to_text gives for the same XML (in
    code points), so `text[start:end]` is the entity's `text`, like an entity
    that the recognition finds. Spaces and line breaks at its ends are left out,
    as tag_text_range leaves them outside the element. An element without text
    is left out.

    Raises InvalidTei if the XML is not a TEI document (see _parse_tei).
    """
    labels = set(get_args(EntityLabel))
    text, found = _marked_elements(
        _text_element(_parse_tei(xml)), lambda node: node.local_name in labels
    )
    entities: list[dict[str, Any]] = []
    for node, start, end in found:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            entities.append(
                {
                    "label": node.local_name,
                    "text": text[start:end],
                    "start": start,
                    "end": end,
                }
            )
    return entities


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


def _serialise(root: TagNode, original: str) -> str:
    """The XML of `root`, with an XML declaration if `original` had one."""
    if original.lstrip().startswith("<?xml"):
        return str(delb.Document(root))
    return str(root)


_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_LANG = f"{{{_XML_NAMESPACE}}}lang"


def _attributes(node: TagNode) -> dict[str, str]:
    """The attributes of an element, by name; an attribute of the XML namespace
    (xml:lang) by its name in Clark notation, `{namespace}lang`."""
    return {
        (f"{{{key[0]}}}{key[1]}" if key[0] == _XML_NAMESPACE else key[1]): str(
            attribute.value
        )
        for key, attribute in node.attributes.items()
    }


def _written(name: str) -> str:
    """The name of an attribute as it is written in the XML (`xml:lang`)."""
    prefix = f"{{{_XML_NAMESPACE}}}"
    return "xml:" + name[len(prefix) :] if name.startswith(prefix) else name


def _locate(
    xml: str, start: int, end: int, text: str, what: str = "selection"
) -> tuple[TagNode, list[_Source], int, int]:
    """Parse the XML and find the text `start` to `end` in it: the root, the
    source of each character of the text, and the range without the spaces and
    line breaks at its ends.

    Raises InvalidTei, TextChanged (if `text` is not what is at the offsets), and
    CannotTag (if the range has no text but spaces).
    """
    root = _parse_tei(xml)
    extracted, sources = _extract(_text_element(root))
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )
    while extracted[start].isspace():
        start += 1
        if start == end:
            raise CannotTag(f"The {what} has no text")
    while extracted[end - 1].isspace():
        end -= 1
    return root, sources, start, end


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
    # A <cit> holds the quotation in a <quote> (it has no text of its own in TEI).
    inner = "quote" if label == "cit" else None
    return _wrap_inline(xml, start, end, text, label, {}, what="entity", inner=inner)


def _wrap_inline(
    xml: str,
    start: int,
    end: int,
    text: str,
    name: str,
    attributes: dict[str, str],
    what: str = "selection",
    inner: str | None = None,
) -> str:
    """The inline wrapping behind tag_text_range, for an element `name` with the
    given attributes (see there for what it does and when it refuses). `what`
    is how the messages call the text: "entity", "selection". With `inner`, the
    text goes into an element of that name inside the new one (<cit><quote>)."""
    root, sources, start, end = _locate(xml, start, end, text, what)
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
        raise CannotTag(f"The {what} spans more than one paragraph or line")
    if isinstance(start_child, TagNode) and not _blank_before(
        start_child, first_node, first_index
    ):
        raise CannotTag(f"The {what} starts inside <{start_child.local_name}>")
    if isinstance(end_child, TagNode) and not _blank_after(
        end_child, last_node, last_index
    ):
        raise CannotTag(f"The {what} ends inside <{end_child.local_name}>")
    whole = all(
        any(node is other for other in covered)
        for node in children
        if not (isinstance(node, TextNode) and not node.content.strip())
    )
    already_in = (parent.local_name == name and _attributes(parent) == attributes) or (
        # the quotation of a <cit> that is just it
        inner is not None
        and parent.local_name == inner
        and parent.parent is not None
        and parent.parent.local_name == name
    )
    if whole and already_in:
        return xml  # already marked, and the whole of the element
    # Comments and processing instructions are not reachable as children, so
    # they could not be moved along with the selection.
    serialised = str(parent)
    if "<!--" in serialised or "<?" in serialised:
        raise CannotTag(
            f"The {what} is in a <{parent.local_name}> that contains a comment or "
            "processing instruction, which cannot be moved"
        )

    # Cut the text nodes at the ends of the selection.
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

    wrapper = TagNode(name, attributes, namespace=TEI_NAMESPACE)
    covered[0].add_preceding_siblings(wrapper)
    for node in covered:
        node.detach()
    if inner is None:
        wrapper.append_children(*covered)
    else:
        inside = TagNode(inner, {}, namespace=TEI_NAMESPACE)
        wrapper.append_children(inside)
        inside.append_children(*covered)

    return _serialise(root, xml)


class CannotReplace(ValueError):
    """The text cannot be replaced in the XML; the message says why."""


# What the new text of a replacement must not contain: line breaks, tabs and
# the other control characters (which XML does not allow anyway).
_CONTROL_CHARACTERS = re.compile("[\x00-\x1f\x7f\ufffe\uffff]")


def replace_text_range(xml: str, start: int, end: int, before: str, after: str) -> str:
    """Replace the characters `start` to `end` of the text of a TEI document,
    which must be `before`, with `after`, and return the changed XML.

    `start` and `end` are character offsets into what tei_xml_to_text returns
    for the same XML. The text is changed where it is, in the text nodes that
    hold it, so the markup around it stays as it is (a word inside
    <persName>...</persName> stays in it). If the text is spread over several
    nodes (a word with inline markup in the middle), the new text goes into the
    first of them and the characters are removed from the others.

    The text to replace must not contain spaces or line breaks (it is a word,
    or a part of one); the new text must not be empty, start or end with a
    space, or contain line breaks or other control characters, and must differ
    from the old one. The one exception is an empty text, which removes the
    token (a stray "$"): if it is between two spaces, one of them is removed too,
    so that there are not two together.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `before`, and CannotReplace for the other problems.
    """
    if after != after.strip():
        raise CannotReplace("The new text must not start or end with a space")
    if _CONTROL_CHARACTERS.search(after):
        raise CannotReplace(
            "The new text must be on one line, without control characters"
        )
    if after == before:
        raise CannotReplace("The new text is the same as the old one")
    root = _parse_tei(xml)
    extracted, sources = _extract(_text_element(root))
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != before:
        raise TextChanged(
            f"The text at {start}-{end} is not {before!r}: the page has changed"
        )
    if any(character.isspace() for character in before):
        raise CannotReplace(
            "The text to replace must not contain spaces or line breaks"
        )

    # Where the characters are: for each text node, from which index to which.
    runs: list[tuple[TextNode, int, int]] = []
    for source in sources[start:end]:
        assert source is not None  # not whitespace: real text
        node, index = source
        if runs and runs[-1][0] is node and runs[-1][2] + 1 == index:
            runs[-1] = (node, runs[-1][1], index)
        elif any(run[0] is node for run in runs):
            raise CannotReplace("The text is not in one piece in the XML")
        else:
            runs.append((node, index, index))

    for position, (node, first, last) in enumerate(runs):
        content = node.content
        # a token between two spaces is removed with one of them, so that no
        # two spaces are left together
        if (
            not after
            and first > 0
            and content[first - 1] == " "
            and content[last + 1 : last + 2] == " "
        ):
            last += 1
        new_content = (
            content[:first] + (after if position == 0 else "") + content[last + 1 :]
        )
        if new_content:
            node.content = new_content
        else:
            node.detach()
    return _serialise(root, xml)


class CannotInsert(ValueError):
    """The text cannot be inserted into the XML; the message says why."""


def _insert_into_empty_page(root: TagNode, token: str, xml: str) -> str:
    """Put `token` into a document that has no text at all (an empty page, the
    OCR found nothing): into its first <p>, which is made if there is none (in
    the <body>, which is made too if needed). It is the first line of the page."""
    text_element = _text_element(root)
    paragraph = next(
        (
            node
            for node in text_element.iterate_descendants()
            if isinstance(node, TagNode) and node.local_name == "p"
        ),
        None,
    )
    if paragraph is None:
        body = next(
            (
                node
                for node in text_element.iterate_descendants()
                if isinstance(node, TagNode) and node.local_name == "body"
            ),
            None,
        )
        if body is None:
            body = TagNode("body", namespace=TEI_NAMESPACE)
            text_element.append_children(body)
        paragraph = TagNode("p", namespace=TEI_NAMESPACE)
        body.append_children(paragraph)
    paragraph.append_children(token)
    return _serialise(root, xml)


def insert_text_at(xml: str, position: int, token: str) -> str:
    """Insert `token`, a piece of text that the OCR missed, at `position` of the
    text of a TEI document, and return the changed XML.

    `position` is an offset into what tei_xml_to_text returns for the same XML,
    between two characters (0 is before the first, and the length of the text
    after the last). The token goes into the text node of the neighbouring
    character, so the markup around stays as it is (a token put after the last
    word of a <persName> goes into it). It is a token of its own: a space is put
    before it unless there is a space or a line break there already, and after
    it unless the text goes on with one, so that "Alama" with a token "X" in
    the middle becomes "Ala X ma".

    The token may be several words ("LIST SZÓSTY"), separated by spaces (a run of
    spaces is one, as in the text that is extracted). It must not be empty, start
    or end with a space, or contain line breaks, tabs or other control or space
    characters. Raises InvalidTei if the XML is not TEI, CannotInsert if the
    token is not acceptable or the position is not in the text, or is where no
    text node can take it (the empty line between two paragraphs).

    A document without any text (an empty page) takes the token as its first
    line: there is no other place for it (position 0 is the only one).
    """
    if not token or token != token.strip():
        raise CannotInsert("The token must not be empty or start or end with a space")
    if _CONTROL_CHARACTERS.search(token) or any(
        c.isspace() and c != " " for c in token
    ):
        raise CannotInsert(
            "The token must be on one line, without control characters or "
            "spaces other than the plain space"
        )
    token = re.sub(" +", " ", token)  # the extracted text has a run as one space
    root = _parse_tei(xml)
    extracted, sources = _extract(_text_element(root))
    if not 0 <= position <= len(extracted):
        raise CannotInsert(f"There is no place {position} in the text")
    if not extracted:
        return _insert_into_empty_page(root, token, xml)

    before = extracted[position - 1] if position > 0 else None
    after = extracted[position] if position < len(extracted) else None
    lead = "" if before is None or before.isspace() else " "
    trail = "" if after is None or after.isspace() else " "
    if position > 0 and sources[position - 1] is not None:
        # right after the character before
        node, index = sources[position - 1]  # type: ignore[misc]
        where = index + 1
    elif position < len(extracted) and sources[position] is not None:
        # right before the character after (at the start of a line or text)
        node, index = sources[position]  # type: ignore[misc]
        where = index
    else:
        raise CannotInsert("There is no place for text between two paragraphs")
    node.content = node.content[:where] + lead + token + trail + node.content[where:]
    return _serialise(root, xml)


class CannotJoin(ValueError):
    """The tokens cannot be joined in the XML; the message says why."""


def join_text_range(xml: str, start: int, end: int, text: str) -> str:
    """Join the tokens in the characters `start` to `end` of the text of a TEI
    document, which must be `text`, into one: the spaces between them are removed
    (OCR sometimes splits one word in two, "abso lutely"), and the changed XML
    is returned.

    `text` is two or more tokens, each separated from the next by exactly one
    space (a run of whitespace in the XML is one space in the text, and all of it
    goes). The spaces are removed where they are, in the text nodes that hold them
    (or the nodes of their own between two inline elements), so the markup around
    stays as it is. Tokens on different lines are not joined: a line break is not
    a space.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `text`, and CannotJoin for the other problems.
    """
    if (
        " " not in text
        or any(c.isspace() and c != " " for c in text)
        or "  " in text
        or text != text.strip()
    ):
        raise CannotJoin("There must be two or more tokens, separated by single spaces")
    root = _parse_tei(xml)
    text_element = _text_element(root)
    extracted, sources = _extract(text_element)
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )

    # the raw characters, to find the whole run of whitespace of each space
    raw: list[_Char] = []
    _flatten(text_element, raw)
    where = {
        (id(source[0]), source[1]): position
        for position, (_, source) in enumerate(raw)
        if source is not None
    }
    doomed: dict[int, tuple[TextNode, set[int]]] = {}
    for offset in range(start, end):
        if extracted[offset] != " ":
            continue
        source = sources[offset]
        assert source is not None  # a space between two tokens is in the XML
        position = where[(id(source[0]), source[1])]
        while position < len(raw) and raw[position][0].isspace():
            node, index = raw[position][1]  # type: ignore[misc]
            doomed.setdefault(id(node), (node, set()))[1].add(index)
            position += 1

    for node, indexes in doomed.values():
        content = "".join(
            char for index, char in enumerate(node.content) if index not in indexes
        )
        if content:
            node.content = content
        else:
            node.detach()
    return _serialise(root, xml)


class CannotSplit(ValueError):
    """The line cannot be split in the XML; the message says why."""


def split_line_at(xml: str, start: int, end: int, text: str) -> str:
    """Start a new line at the space between two tokens of the same line: the
    space, in the characters `start` to `end` of the text of a TEI document, is
    replaced with a line break (<lb/>), and the changed XML is returned.

    `text` is the two tokens as the client saw them, separated by one space, and
    the characters `start` to `end` of the text must be that (TextChanged if they
    are not: the page has changed). The whole run of whitespace of the space in
    the XML goes, and the <lb/> is put where it was, so the markup around stays
    as it is (a line may be split inside a <persName>). A token before the break
    that ends with a hyphen continues a word on the next line: the break is then
    <lb break="no"/>, as text_to_tei_xml makes it.

    There are two tokens on one line, so the new line is never empty: the
    start or the end of a line, a line break and a paragraph break are not places
    for it. Raises InvalidTei if the XML is not TEI, TextChanged if the text at
    the offsets is not `text`, and CannotSplit for the other problems.
    """
    tokens = text.split(" ")
    if (
        len(tokens) != 2
        or not all(tokens)
        or any(c.isspace() and c != " " for c in text)
    ):
        raise CannotSplit("There must be two tokens, separated by one space")
    root = _parse_tei(xml)
    text_element = _text_element(root)
    extracted, sources = _extract(text_element)
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )

    # the whole run of whitespace of the space, in the raw characters
    raw: list[_Char] = []
    _flatten(text_element, raw)
    where = {
        (id(source[0]), source[1]): position
        for position, (_, source) in enumerate(raw)
        if source is not None
    }
    source = sources[start + len(tokens[0])]
    assert source is not None  # a space between two tokens is in the XML
    position = where[(id(source[0]), source[1])]
    run: list[tuple[TextNode, int]] = []
    while position < len(raw) and raw[position][0].isspace():
        run.append(raw[position][1])  # type: ignore[arg-type]
        position += 1

    first_node, first_index = run[0]
    doomed: dict[int, tuple[TextNode, set[int]]] = {}
    for node, index in run:
        doomed.setdefault(id(node), (node, set()))[1].add(index)
    for node, indexes in doomed.values():
        if node is first_node:
            continue
        content = "".join(c for i, c in enumerate(node.content) if i not in indexes)
        if content:
            node.content = content
        else:
            node.detach()

    # the line break goes where the first whitespace character was
    content = first_node.content
    left = content[:first_index]
    right = "".join(
        c
        for i, c in enumerate(content)
        if i >= first_index and i not in doomed[id(first_node)][1]
    )
    attributes = {"break": "no"} if tokens[0].endswith("-") else {}
    line_break = TagNode("lb", attributes, namespace=TEI_NAMESPACE)
    if left:
        first_node.content = left
        first_node.add_following_siblings(line_break)
        if right:
            line_break.add_following_siblings(right)
    else:
        first_node.add_preceding_siblings(line_break)
        if right:
            first_node.content = right
        else:
            first_node.detach()
    return _serialise(root, xml)


class CannotJoinLines(ValueError):
    """The lines cannot be joined in the XML; the message says why."""


_LINE_GAP = re.compile(r"(\S+)(\n{1,2})(\S+)")


def _block_of(node: Any, text_element: TagNode) -> TagNode | None:
    """The nearest block element (<p>, <head>, ...) that holds the node."""
    parent = node.parent
    while parent is not None and parent is not text_element:
        if parent.local_name in _BLOCK_ELEMENTS:
            return parent
        parent = parent.parent
    return None


def join_lines_at(xml: str, start: int, end: int, text: str) -> str:
    """Remove a line break of a TEI document: the one between the last token of a
    line and the first token of the next one, in the characters `start` to `end`
    of the text, or the empty line between two paragraphs. It undoes
    `split_line_at`. The changed XML is returned.

    `text` is the two tokens as the client saw them, separated by one line break
    ("kota\nA") or by an empty line ("315\n\nbłędy"), and the characters `start`
    to `end` of the text must be that (TextChanged if they are not: the page has
    changed). The markup around stays as it is.

    - A line break (<lb/>) is replaced with one space (also where it was
      <lb break="no"/>: the hyphen stays in the text, and "Join with next" takes
      the space away). A break inside a <persName> goes and the element stays.
    - An empty line is the end of one <p> and the start of the next: the two
      paragraphs become one, with a line break (<lb/>) between them, so the empty
      line is gone and the two lines stay lines. That is "one line break less",
      as the text goes from "\n\n" to "\n": a second removal joins the lines.
      The two must be paragraphs (<p>) next to each other, with nothing between
      them but empty elements. A hyphen at the end of the first one makes the
      break <lb break="no"/>, as in `split_line_at`.

    Only a plain <lb/> goes: the end of an <l> or an <item> is not removed.
    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `text`, and CannotJoinLines for the other problems.
    """
    match = _LINE_GAP.fullmatch(text)
    if match is None:
        raise CannotJoinLines(
            "There must be two tokens, separated by one line break or an empty line"
        )
    first_token, gap, _ = match.groups()
    root = _parse_tei(xml)
    text_element = _text_element(root)
    extracted, sources = _extract(text_element)
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )

    first_at = start + len(first_token)  # the first character of the gap
    before, after = sources[first_at - 1], sources[first_at + len(gap)]
    assert before is not None and after is not None  # real text on both sides
    if len(gap) == 2:
        _merge_paragraphs(text_element, before[0], after[0], first_token)
        return _serialise(root, xml)

    # the <lb/> is between the two text nodes, in the order of the document
    nodes = list(text_element.iterate_descendants())
    first = next(i for i, node in enumerate(nodes) if node is before[0])
    last = next(i for i, node in enumerate(nodes) if node is after[0])
    breaks = [
        node
        for node in nodes[first + 1 : last]
        if isinstance(node, TagNode) and node.local_name == "lb"
    ]
    if len(breaks) != 1:
        raise CannotJoinLines(
            "The line break is not a plain <lb/>: it cannot be removed"
        )
    breaks[0].add_preceding_siblings(" ")
    breaks[0].detach()
    return _serialise(root, xml)


class CannotRemoveLine(ValueError):
    """The line cannot be removed from the XML; the message says why."""


def remove_line_at(xml: str, start: int, end: int, text: str) -> str:
    """Remove a whole line of a TEI document: the characters `start` to `end` of
    the text, which are `text`, and the line break that goes with it. The changed
    XML is returned.

    `text` is the line as the client saw it (no line break in it, and no space at
    its ends), and the characters `start` to `end` of the text must be that, and
    be a whole line: the text before them ends with a line break (or they start
    the text) and the text after them starts with one (or they end it); else
    TextChanged or CannotRemoveLine. The line must be text of one block (<p>,
    <head>, <ab> or <lg>) and have no markup inside it: a line with an element in
    it (a <persName>) is not removed.

    The line break that goes is the <lb/> before the line, or after it if it is the
    first line of its block. A line that is all of its block takes the block with
    it (the empty line next to it goes too), unless it is the only block of the
    page, which stays, empty.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `text`, and CannotRemoveLine for the other problems.
    """
    if not text or "\n" in text or text != text.strip():
        raise CannotRemoveLine(
            "The line must not be empty, have a line break, or start or end with "
            "a space"
        )
    root = _parse_tei(xml)
    text_element = _text_element(root)
    extracted, sources = _extract(text_element)
    if not 0 <= start < end <= len(extracted) or extracted[start:end] != text:
        raise TextChanged(
            f"The text at {start}-{end} is not {text!r}: the page has changed"
        )
    if (start > 0 and extracted[start - 1] != "\n") or (
        end < len(extracted) and extracted[end] != "\n"
    ):
        raise CannotRemoveLine("The text is not a whole line")

    # where the characters of the line are, in each text node
    spans: dict[int, tuple[TextNode, int, int]] = {}
    for source in sources[start:end]:
        if source is None:
            continue
        node, index = source
        _, first, last = spans.get(id(node), (node, index, index))
        spans[id(node)] = (node, min(first, index), max(last, index))
    parents = {id(node.parent) for node, _, _ in spans.values()}
    if len(parents) != 1:
        raise CannotRemoveLine("The line has markup in it: it cannot be removed")
    block = next(iter(spans.values()))[0].parent
    if block is None or block.local_name not in _BLOCK_ELEMENTS - {"div"}:
        raise CannotRemoveLine("The line is not in a paragraph: it cannot be removed")

    children = list(block.iterate_children())
    mine = {id(node) for node, _, _ in spans.values()}
    positions = [i for i, child in enumerate(children) if id(child) in mine]
    first_at, last_at = positions[0], positions[-1]
    if any(
        id(child) not in mine and not _is_blank(child) and not _is_lb(child)
        for child in children[first_at : last_at + 1]
    ):
        raise CannotRemoveLine("The line has markup in it: it cannot be removed")

    def neighbour(order: range) -> Any:
        for i in order:
            if not _is_blank(children[i]):
                return children[i]
        return None

    before = neighbour(range(first_at - 1, -1, -1))
    after = neighbour(range(last_at + 1, len(children)))
    if _is_lb(before):
        gone = before
    elif before is None and _is_lb(after):
        gone = after
    elif before is None and after is None:
        gone = None  # the line is all of its block
    else:
        raise CannotRemoveLine(
            "The line has other markup next to it: it cannot be removed"
        )

    for node, first, last in spans.values():
        content = node.content
        rest = content[:first] + content[last + 1 :]
        if rest.strip():
            node.content = rest
        else:
            node.detach()
    if gone is not None:
        gone.detach()
    else:
        blocks = [
            node
            for node in text_element.iterate_descendants()
            if isinstance(node, TagNode) and node.local_name in _BLOCK_ELEMENTS
        ]
        if len(blocks) > 1:
            block.detach()
    return _serialise(root, xml)


def _is_lb(node: Any) -> bool:
    return isinstance(node, TagNode) and node.local_name == "lb"


def _merge_paragraphs(
    text_element: TagNode, before: Any, after: Any, first_token: str
) -> None:
    """Make the paragraph of the node `before` and the one of `after`, which
    follows it, one paragraph, with an <lb/> between the two."""
    upper = _block_of(before, text_element)
    lower = _block_of(after, text_element)
    if (
        upper is None
        or lower is None
        or upper.local_name != "p"
        or lower.local_name != "p"
        or upper is lower
        or upper.parent is not lower.parent
    ):
        raise CannotJoinLines(
            "The empty line is between two paragraphs (<p>) that are next to each "
            "other: it cannot be removed here"
        )
    siblings = list(upper.parent.iterate_children())
    between = siblings[
        next(i for i, node in enumerate(siblings) if node is upper) + 1 : next(
            i for i, node in enumerate(siblings) if node is lower
        )
    ]
    for node in between:
        if isinstance(node, TextNode):
            holds_text = bool(node.content.strip())
        else:
            holds_text = isinstance(node, TagNode) and any(
                n.content.strip() for n in _text_nodes(node)
            )
        if holds_text:
            raise CannotJoinLines("There is other text between the two paragraphs")

    attributes = {"break": "no"} if first_token.endswith("-") else {}
    upper.append_children(TagNode("lb", attributes, namespace=TEI_NAMESPACE))
    for child in list(lower.iterate_children()):
        upper.append_children(child.detach())
    for node in between:
        node.detach()
    lower.detach()


# The sections of a page that can be marked, by the name the API gives them: the
# element they become and its attributes. A running header or footer and a
# printed page number are forme work (<fw>) in TEI, and a note is a <note> (a
# `note_group` is a <noteGrp>, which holds only notes).
SECTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "header": ("fw", {"type": "header"}),
    "footer": ("fw", {"type": "footer"}),
    "page_number": ("fw", {"type": "pageNum"}),
    "toc": ("div", {"type": "toc"}),
    "note_group": ("noteGrp", {}),
    "note": ("note", {}),
    # a quotation in another language: the language is given with it (`lang`)
    "quote": ("quote", {}),
}


def foreign_language_ranges(xml: str) -> list[tuple[int, int]]:
    """The stretches of the text (as tei_xml_to_text gives it) that are quotations
    in another language than Polish, `<quote xml:lang="la">`: `start` and `end`,
    in code points, in the order of the document (outer ones first).

    Raises InvalidTei if the XML is not a TEI document (see _parse_tei)."""

    def foreign(node: TagNode) -> bool:
        lang = _attributes(node).get(_LANG) if node.local_name == "quote" else None
        return lang is not None and lang.lower().split("-")[0] != "pl"

    _, found = _marked_elements(_text_element(_parse_tei(xml)), foreign)
    return [(start, end) for _, start, end in found]


def _blocks_around(node: TextNode, text_element: TagNode) -> TagNode | None:
    """The nearest block (paragraph, ...) that contains the text node."""
    parent = node.parent
    while parent is not None and parent is not text_element:
        if parent.local_name in _BLOCK_ELEMENTS:
            return parent
        parent = parent.parent
    return None


def _is_blank(node: Any) -> bool:
    return isinstance(node, TextNode) and not node.content.strip()


def _leading(node: Any) -> bool:
    """Is it a line break or spaces: what only separates lines?"""
    return _is_blank(node) or (isinstance(node, TagNode) and node.local_name == "lb")


def _split_before(block: TagNode, boundary: Any) -> TagNode:
    """Cut `block` in two before its child `boundary`: the part from `boundary`
    on moves into a new element like `block`, right after it, which is returned."""
    nodes = []
    node: Any = boundary
    while node is not None:
        nodes.append(node)
        node = node.fetch_following_sibling()
    part = TagNode(block.local_name, _attributes(block), namespace=TEI_NAMESPACE)
    block.add_following_siblings(part)
    for node in nodes:
        node.detach()
    part.append_children(*nodes)
    return part


def _wrap_blocks(
    xml: str, start: int, end: int, text: str, name: str, attributes: dict[str, str]
) -> str:
    """Put whole paragraphs inside an element `name` (a <div>), for the
    characters `start` to `end` of the text (see mark_section)."""
    root, sources, start, end = _locate(xml, start, end, text)
    first, last = sources[start], sources[end - 1]
    assert first is not None and last is not None
    (first_node, first_index), (last_node, last_index) = first, last
    text_element = _text_element(root)

    first_block = _blocks_around(first_node, text_element)
    last_block = _blocks_around(last_node, text_element)
    for block in (first_block, last_block):
        if block is None or block.local_name != "p":
            where = (
                "outside a paragraph" if block is None else f"in a <{block.local_name}>"
            )
            raise CannotTag(f"The selection starts or ends {where}, not in a paragraph")
    assert first_block is not None and last_block is not None
    container = first_block.parent
    if last_block.parent is not container:
        raise CannotTag(
            "The selection spans paragraphs in different divisions of the page"
        )

    # Where the selection starts and ends within its paragraphs: the child of the
    # paragraph, and whether the text of that child starts/ends the selection.
    start_child = _child_of(first_block, first_node)
    end_child = _child_of(last_block, last_node)
    if isinstance(start_child, TagNode) and not _blank_before(
        start_child, first_node, first_index
    ):
        raise CannotTag(f"The selection starts inside <{start_child.local_name}>")
    if isinstance(end_child, TagNode) and not _blank_after(
        end_child, last_node, last_index
    ):
        raise CannotTag(f"The selection ends inside <{end_child.local_name}>")

    def has_real_content(nodes: list[Any]) -> bool:
        return any(not _leading(node) for node in nodes)

    def siblings(block: TagNode) -> list[Any]:
        return list(block.iterate_children())

    starts_block = not has_real_content(
        siblings(first_block)[: siblings(first_block).index(start_child)]
    ) and not (
        isinstance(start_child, TextNode) and start_child.content[:first_index].strip()
    )
    ends_block = not has_real_content(
        siblings(last_block)[siblings(last_block).index(end_child) + 1 :]
    ) and not (
        isinstance(end_child, TextNode) and end_child.content[last_index + 1 :].strip()
    )

    # Already exactly the content of such a div: nothing to do.
    if (
        container.local_name == name
        and _attributes(container) == attributes
        and starts_block
        and ends_block
        and all(
            any(
                node is other
                for other in _blocks_between(container, first_block, last_block)
            )
            for node in container.iterate_children()
            if not _is_blank(node)
        )
    ):
        return xml

    for block, splitting in (
        (first_block, not starts_block),
        (last_block, not ends_block),
    ):
        serialised = str(block)
        if splitting and ("<!--" in serialised or "<?" in serialised):
            raise CannotTag(
                "The paragraph contains a comment or processing instruction, "
                "which cannot be moved when the paragraph is split"
            )

    # Cut the end first (it may be in the same paragraph as the start): a text
    # node is cut after the selection, and the part after it becomes a paragraph
    # of its own, with the line break that starts its first line.
    selected_last = last_block
    if not ends_block:
        if (
            isinstance(end_child, TextNode)
            and end_child.content[last_index + 1 :].strip()
        ):
            tail = end_child.content[last_index + 1 :]
            end_child.content = end_child.content[: last_index + 1]
            (boundary,) = end_child.add_following_siblings(tail)
        else:
            boundary = end_child.fetch_following_sibling()
            while boundary is not None and _is_blank(boundary):
                boundary = boundary.fetch_following_sibling()
        if boundary is not None:
            _split_before(last_block, boundary)

    selected_first = first_block
    if not starts_block:
        if (
            isinstance(start_child, TextNode)
            and start_child.content[:first_index].strip()
        ):
            head = start_child.content[:first_index]
            rest = start_child.content[first_index:]
            start_child.content = head
            (boundary,) = start_child.add_following_siblings(rest)
        else:
            boundary = start_child
        # a line break belongs to the line it starts, so it goes with it
        previous = boundary.fetch_preceding_sibling()
        while previous is not None and _leading(previous):
            boundary = previous
            previous = boundary.fetch_preceding_sibling()
        selected_first = _split_before(first_block, boundary)
        if first_block is last_block:
            selected_last = selected_first

    selected = _blocks_between(container, selected_first, selected_last)
    wrapper = TagNode(name, attributes, namespace=TEI_NAMESPACE)
    selected[0].add_preceding_siblings(wrapper)
    for node in selected:
        node.detach()
    wrapper.append_children(*selected)
    _keep_divs_valid(container)
    return _serialise(root, xml)


def _blocks_between(container: TagNode, first: TagNode, last: TagNode) -> list[Any]:
    """The children of `container` from `first` to `last`, both included."""
    children = list(container.iterate_children())
    i = next(i for i, child in enumerate(children) if child is first)
    j = next(i for i, child in enumerate(children) if child is last)
    return children[i : j + 1]


def _keep_divs_valid(container: TagNode) -> None:
    """In TEI nothing but <div>s (and <fw>) may follow a <div> in the same
    element, so the paragraphs after the first <div> of `container` are each put
    in a plain <div>, which is what keeps the document valid."""
    run: list[Any] = []
    seen_div = False
    for child in list(container.iterate_children()):
        if not isinstance(child, TagNode):
            continue  # text (spaces): stays where it is
        if child.local_name == "div":
            seen_div = True
        if seen_div and child.local_name not in ("div", "fw"):
            run.append(child)
            continue
        if run:
            _wrap_in_div(run)
            run = []
    if run:
        _wrap_in_div(run)


def _wrap_in_div(nodes: list[Any]) -> None:
    div = TagNode("div", namespace=TEI_NAMESPACE)
    nodes[0].add_preceding_siblings(div)
    for node in nodes:
        node.detach()
    div.append_children(*nodes)


def _wrap_notes(xml: str, start: int, end: int, text: str) -> str:
    """Put the notes of the selection in a <noteGrp>. TEI lets a <noteGrp> hold
    only notes (no text, not even an <lb/>), so the selection must be notes that
    are already marked, next to each other, and nothing else but spaces and line
    breaks; a line break between notes is kept as a newline in the text."""
    root, sources, start, end = _locate(xml, start, end, text)
    first, last = sources[start], sources[end - 1]
    assert first is not None and last is not None
    (first_node, first_index), (last_node, last_index) = first, last

    parent = _common_parent(first_node, last_node)
    start_child = _child_of(parent, first_node)
    end_child = _child_of(parent, last_node)
    if parent.local_name == "note" and parent.parent is not None:
        # inside one note: the group is of that note, if it is all of it
        whole = _blank_before(parent, first_node, first_index) and _blank_after(
            parent, last_node, last_index
        )
        if whole:
            start_child = end_child = parent
            parent = parent.parent
    children = list(parent.iterate_children())
    begin = next(i for i, c in enumerate(children) if c is start_child)
    finish = next(i for i, c in enumerate(children) if c is end_child)
    covered = children[begin : finish + 1]

    only_notes = (
        isinstance(start_child, TagNode)
        and start_child.local_name == "note"
        and isinstance(end_child, TagNode)
        and end_child.local_name == "note"
        and all(
            (isinstance(c, TagNode) and c.local_name in {"note", "lb"})
            or (isinstance(c, TextNode) and not c.content.strip())
            for c in covered
        )
    )
    inside_ok = only_notes and (
        start_child is not first_node
        and _blank_before(start_child, first_node, first_index)
        and _blank_after(end_child, last_node, last_index)
    )
    if not inside_ok:
        raise CannotTag(
            "A note group holds only notes: select notes that are already marked "
            "(and nothing else), next to each other"
        )
    if parent.local_name == "noteGrp":
        if all(
            any(c is o for o in covered)
            for c in children
            if not (isinstance(c, TextNode) and not c.content.strip())
        ):
            return xml  # already a group, and all of it
        raise CannotTag("The notes are already in a note group")
    serialised = str(parent)
    if "<!--" in serialised or "<?" in serialised:
        raise CannotTag(
            f"The selection is in a <{parent.local_name}> that contains a comment "
            "or processing instruction, which cannot be moved"
        )

    # A <noteGrp> may not hold a line break (<lb/>) between its notes, so each
    # becomes a line break in the text, which keeps the text as it was.
    for i, node in enumerate(covered):
        if isinstance(node, TagNode) and node.local_name == "lb":
            (covered[i],) = node.add_preceding_siblings("\n")
            node.detach()
    wrapper = TagNode("noteGrp", {}, namespace=TEI_NAMESPACE)
    covered[0].add_preceding_siblings(wrapper)
    for node in covered:
        node.detach()
    wrapper.append_children(*covered)
    return _serialise(root, xml)


def mark_section(
    xml: str,
    start: int,
    end: int,
    section: str,
    text: str,
    n: str | None = None,
    lang: str | None = None,
) -> str:
    """Mark the characters `start` to `end` of the text of a TEI document as a
    section of the page, and return the changed XML.

    `section` is one of SECTIONS: a running `header` or `footer` and the printed
    `page_number` become <fw type="header">, <fw type="footer"> and
    <fw type="pageNum"> around the text, `toc` (the table of contents) becomes
    <div type="toc">, a `note` becomes <note> (with `n="..."` if `n` is given,
    the number of the note), and a `note_group` becomes <noteGrp> around notes
    that are already marked (TEI lets it hold nothing else), and a `quote` becomes
    <quote xml:lang="..."> around the text, a quotation in another language (the
    code of the language, one of LANGUAGES, is `lang`, and is needed for a quote
    and only for it). `start` and `end`
    are character offsets into what tei_xml_to_text returns for the same XML,
    and `text` is what is expected there; if it is not, the text has changed
    since the offsets were taken.

    The <fw> elements go around the text itself, like an entity does (see
    tag_text_range), so the text does not change, and what they cannot do is
    start or end inside other markup or span paragraphs.

    A <div> may not contain bare text, nor be inside a paragraph, so it goes
    around whole paragraphs: a paragraph that the selection starts or ends in the
    middle of is split in two there (the line break that starts a line goes with
    that line), and the paragraphs of the selection are put in the <div>. The
    text then has a blank line where a paragraph was split. Because TEI does not
    allow paragraphs after a <div>, the paragraphs that follow are put in a plain
    <div> each.

    If the selection is already exactly such a section, nothing is changed.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `text`, and CannotTag if the section cannot be marked.
    """
    if section not in get_args(SectionKind):
        raise CannotTag(f"{section!r} is not a section of a page")
    name, attributes = SECTIONS[section]
    if n is not None:
        if section != "note":
            raise CannotTag("Only a note has a number")
        attributes = {**attributes, "n": n}
    if section == "quote":
        if lang not in LANGUAGES:
            raise CannotTag(
                "A quote needs the language it is in, one of: " + ", ".join(LANGUAGES)
            )
        attributes = {_LANG: lang}
    elif lang is not None:
        raise CannotTag("Only a quote has a language")
    if name == "div":
        return _wrap_blocks(xml, start, end, text, name, attributes)
    if name == "noteGrp":
        return _wrap_notes(xml, start, end, text)
    return _wrap_inline(xml, start, end, text, name, attributes)
