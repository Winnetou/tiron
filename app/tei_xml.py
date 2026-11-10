"""Conversion between page text and TEI XML, using delb."""

import re
from typing import Any, get_args

import delb
from delb import TagNode, tag
from delb.nodes import TextNode

from app.models import EntityLabel, SectionKind

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


def _serialise(root: TagNode, original: str) -> str:
    """The XML of `root`, with an XML declaration if `original` had one."""
    if original.lstrip().startswith("<?xml"):
        return str(delb.Document(root))
    return str(root)


def _attributes(node: TagNode) -> dict[str, str]:
    """The attributes of an element, by name."""
    return {key[1]: str(attribute.value) for key, attribute in node.attributes.items()}


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
    return _wrap_inline(xml, start, end, text, label, {}, what="entity")


def _wrap_inline(
    xml: str,
    start: int,
    end: int,
    text: str,
    name: str,
    attributes: dict[str, str],
    what: str = "selection",
) -> str:
    """The inline wrapping behind tag_text_range, for an element `name` with the
    given attributes (see there for what it does and when it refuses). `what`
    is how the messages call the text: "entity", "selection"."""
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
    if (
        parent.local_name == name
        and _attributes(parent) == attributes
        and all(
            any(node is other for other in covered)
            for node in children
            if not (isinstance(node, TextNode) and not node.content.strip())
        )
    ):
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
    wrapper.append_children(*covered)

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
    from the old one.

    Raises InvalidTei if the XML is not TEI, TextChanged if the text at the
    offsets is not `before`, and CannotReplace for the other problems.
    """
    if not after:
        raise CannotReplace("The new text is empty")
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
        new_content = (
            content[:first] + (after if position == 0 else "") + content[last + 1 :]
        )
        if new_content:
            node.content = new_content
        else:
            node.detach()
    return _serialise(root, xml)


# The sections of a page that can be marked, by the name the API gives them: the
# element they become and its attributes. A running header or footer and a
# printed page number are forme work (<fw>) in TEI.
SECTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "header": ("fw", {"type": "header"}),
    "footer": ("fw", {"type": "footer"}),
    "page_number": ("fw", {"type": "pageNum"}),
    "toc": ("div", {"type": "toc"}),
}


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


def mark_section(xml: str, start: int, end: int, section: str, text: str) -> str:
    """Mark the characters `start` to `end` of the text of a TEI document as a
    section of the page, and return the changed XML.

    `section` is one of SECTIONS: a running `header` or `footer` and the printed
    `page_number` become <fw type="header">, <fw type="footer"> and
    <fw type="pageNum"> around the text, and `toc` (the table of contents)
    becomes <div type="toc">. `start` and `end` are character offsets into what
    tei_xml_to_text returns for the same XML, and `text` is what is expected
    there; if it is not, the text has changed since the offsets were taken.

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
    if name == "div":
        return _wrap_blocks(xml, start, end, text, name, attributes)
    return _wrap_inline(xml, start, end, text, name, attributes)
