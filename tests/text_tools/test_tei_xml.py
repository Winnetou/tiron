import random
import re

import delb
import pytest

from app.text_tools.tei_xml import (
    TEI_NAMESPACE,
    CannotInsert,
    CannotJoin,
    CannotJoinLines,
    CannotReplace,
    CannotSplit,
    CannotTag,
    InvalidTei,
    TextChanged,
    insert_text_at,
    join_lines_at,
    join_text_range,
    mark_section,
    replace_text_range,
    split_line_at,
    tag_text_range,
    tei_xml_entities,
    tei_xml_sections,
    tei_xml_to_text,
    text_to_tei_xml,
)

TEXT = (
    "84 KATEDRA\n"
    "\n"
    "cle przekazał jey krzyż złoty, drogimi kamie\n"
    "niami sadzony, (ze zdobyczy w Lwowie), z\n"
    "znaczną cząstką drzewa krzyża ś., który w o-\n"
    "wych czasach 10. tysięcy zł. ceniono.\n"
)


def body(document: delb.Document) -> str:
    """The serialised <body> of the document."""
    xml = str(document)
    return xml[xml.index("<body>") : xml.index("</body>") + len("</body>")]


def test_returns_a_tei_document():
    document = text_to_tei_xml(TEXT)

    assert isinstance(document, delb.Document)
    assert document.root.universal_name == f"{{{TEI_NAMESPACE}}}TEI"
    assert str(document).startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert f'<TEI xmlns="{TEI_NAMESPACE}">' in str(document)


def test_has_a_minimal_header_and_the_text():
    xml = str(text_to_tei_xml(TEXT))

    for element in (
        "<teiHeader><fileDesc><titleStmt><title>OCR text of a page</title>",
        "</titleStmt><publicationStmt><p>Unpublished</p></publicationStmt>",
        "</fileDesc></teiHeader><text><body>",
    ):
        assert element in xml


def test_has_no_source_description():
    xml = str(text_to_tei_xml(TEXT))

    assert "sourceDesc" not in xml
    assert "polona.pl" not in xml


def test_blank_lines_separate_paragraphs():
    assert body(text_to_tei_xml(TEXT)).count("<p>") == 2
    assert "<p>84 KATEDRA</p>" in body(text_to_tei_xml(TEXT))


def test_line_breaks_become_lb_elements():
    result = body(text_to_tei_xml(TEXT))

    assert (
        "<p>cle przekazał jey krzyż złoty, drogimi kamie<lb/>"
        "niami sadzony, (ze zdobyczy w Lwowie), z<lb/>"
        'znaczną cząstką drzewa krzyża ś., który w o-<lb break="no"/>'
        "wych czasach 10. tysięcy zł. ceniono.</p>"
    ) in result


def test_hyphenated_line_end_marks_the_word_as_continued():
    result = body(text_to_tei_xml("zna-\nczy to\nwięc"))

    assert '<p>zna-<lb break="no"/>czy to<lb/>więc</p>' in result


def test_special_characters_are_escaped_and_survive_a_roundtrip():
    document = text_to_tei_xml("a < b & c > d\n\"quoted\" 'text'")

    reparsed = delb.Document(str(document))

    assert "&lt;" in str(document)
    assert "a < b & c > d" in reparsed.root.full_text
    assert "\"quoted\" 'text'" in reparsed.root.full_text


def test_output_is_well_formed_xml():
    document = text_to_tei_xml(TEXT)

    assert delb.Document(str(document)).root.universal_name == (
        f"{{{TEI_NAMESPACE}}}TEI"
    )


def test_surrounding_whitespace_of_lines_is_dropped():
    assert "<p>a<lb/>b</p>" in body(text_to_tei_xml("  a  \n\t b\t"))


def test_windows_line_endings():
    result = body(text_to_tei_xml("one\r\ntwo\r\n\r\nthree"))

    assert "<p>one<lb/>two</p>" in result
    assert "<p>three</p>" in result


def test_whitespace_only_blank_lines_also_separate_paragraphs():
    assert body(text_to_tei_xml("one\n   \t\ntwo")).count("<p>") == 2


def test_characters_not_allowed_in_xml_are_removed():
    document = text_to_tei_xml("Ala\x0c ma\x00 kota")

    assert "<p>Ala ma kota</p>" in body(document)
    delb.Document(str(document))  # still parses


@pytest.mark.parametrize("text", ["", "   ", "\n\n", " \n \n\t"])
def test_empty_text_gives_a_valid_document_with_an_empty_paragraph(text):
    document = text_to_tei_xml(text)

    assert body(document) == "<body><p/></body>"


HEADER = (
    "<teiHeader><fileDesc><titleStmt><title>T</title></titleStmt>"
    "</fileDesc></teiHeader>"
)


def tei(body: str, header: str = HEADER) -> str:
    return (
        f'<TEI xmlns="{TEI_NAMESPACE}">{header}<text><body>{body}</body></text></TEI>'
    )


@pytest.mark.parametrize(
    "text",
    [
        TEXT.strip(),
        "single line",
        'a < b & c > d\n\nsecond "paragraph"',
        "zna-\nczy to\nwięc\n\nnext",
    ],
)
def test_text_roundtrip(text):
    assert tei_xml_to_text(str(text_to_tei_xml(text))) == text


def test_empty_text_roundtrip():
    assert tei_xml_to_text(str(text_to_tei_xml(""))) == ""


def test_extracts_only_the_text_not_the_header():
    xml = tei("<p>body text</p>", HEADER.replace("<title>T", "<title>HEADER TITLE"))

    assert tei_xml_to_text(xml) == "body text"


def test_paragraphs_and_line_breaks():
    xml = tei("<p>one<lb/>two<lb break='no'/>three</p><p>four</p>")

    assert tei_xml_to_text(xml) == "one\ntwo\nthree\n\nfour"


def test_indented_xml_gives_the_same_text_as_compact_xml():
    compact = tei("<head>Title</head><p>one two<lb/>three</p>")
    indented = tei(
        """
        <head>
            Title
        </head>
        <p>
            one   two
            <lb/>
            three
        </p>
        """
    )

    assert tei_xml_to_text(indented) == tei_xml_to_text(compact)
    assert tei_xml_to_text(compact) == "Title\n\none two\nthree"


def test_inline_markup_is_flattened_into_the_text():
    xml = tei(
        "<p>a <hi rend='italic'>very</hi> "
        "<persName>Jan <surname>Kowalski</surname></persName> x</p>"
    )

    assert tei_xml_to_text(xml) == "a very Jan Kowalski x"


def test_verse_lists_and_comments():
    xml = tei(
        "<lg><l>verse 1</l><l>verse 2</l></lg>"
        "<!-- not text --><list><item>a</item><item>b</item></list>"
    )

    assert tei_xml_to_text(xml) == "verse 1\nverse 2\n\na\nb"


def test_entities_and_cdata():
    xml = tei("<p>fish &amp; chips &lt;3 <![CDATA[<raw> & text]]></p>")

    assert tei_xml_to_text(xml) == "fish & chips <3 <raw> & text"


def test_accepts_an_xml_declaration():
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + tei("<p>ok</p>")

    assert tei_xml_to_text(xml) == "ok"


def test_document_without_text_content_gives_empty_text():
    assert tei_xml_to_text(tei("")) == ""


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ("", "empty"),
        ("   \n", "empty"),
        ("hello", "not well-formed"),
        ("<TEI xmlns='x'><text>", "not well-formed"),
        (f"<TEI xmlns='{TEI_NAMESPACE}'><teiHeader/><text></TEI>", "not well-formed"),
        ("<a/>", "root element must be <TEI>"),
        ("<TEI/>", "root element must be <TEI>"),  # no TEI namespace
        (f"<tei xmlns='{TEI_NAMESPACE}'/>", "root element must be <TEI>"),
        (f"<TEI xmlns='{TEI_NAMESPACE}'><text/></TEI>", "no <teiHeader>"),
        (f"<TEI xmlns='{TEI_NAMESPACE}'><teiHeader/></TEI>", "no <text>"),
        (
            f"<TEI xmlns='{TEI_NAMESPACE}'><teiHeader/><x:text xmlns:x='urn:x'/></TEI>",
            "no <text>",
        ),  # a text in another namespace
    ],
)
def test_invalid_tei_is_rejected(xml, message):
    with pytest.raises(InvalidTei, match=message):
        tei_xml_to_text(xml)


@pytest.mark.parametrize(
    "prefix",
    [
        "<!DOCTYPE TEI>",
        "<!doctype tei>",
        '<!DOCTYPE TEI [<!ENTITY x "boom">]>',
        '<!ENTITY y "z">',
    ],
)
def test_dtds_and_entity_declarations_are_rejected(prefix):
    with pytest.raises(InvalidTei, match="DOCTYPE and ENTITY"):
        tei_xml_to_text(prefix + tei("<p>&x;</p>"))


def test_error_message_gives_the_position():
    with pytest.raises(InvalidTei, match=r"line 1, column \d+"):
        tei_xml_to_text("<TEI><p>")


@pytest.mark.parametrize(
    "not_xml", ["/etc/passwd", "http://example.com/x.xml", "file:///etc/hosts"]
)
def test_paths_and_urls_are_never_loaded(not_xml):
    # delb's Document() would treat such strings as a file or a URL
    with pytest.raises(InvalidTei, match="not well-formed"):
        tei_xml_to_text(not_xml)


# --- the extraction keeps tei_xml_to_text's result -------------------------


def reference_text(xml: str) -> str:
    """The extraction as it was written before characters were tracked."""
    import re

    from delb import TagNode
    from delb.nodes import TextNode

    from app.text_tools import tei_xml

    root = tei_xml._parse_tei(xml)
    parts: list[str] = []

    def collect(node):
        for child in node.iterate_children():
            if isinstance(child, TextNode):
                parts.append(child.content)
            elif isinstance(child, TagNode):
                name = child.local_name
                if name in tei_xml._BLOCK_ELEMENTS:
                    parts.append("\x00")
                collect(child)
                if name in tei_xml._BLOCK_ELEMENTS:
                    parts.append("\x00")
                elif name in tei_xml._LINE_ELEMENTS:
                    parts.append("\x01")

    collect(tei_xml._text_element(root))
    paragraphs = []
    for block in "".join(parts).split("\x00"):
        block = re.sub(r"\s+", " ", block)
        block = block.replace("\x01", "\n")
        block = re.sub(r" *\n *", "\n", block).strip()
        if block:
            paragraphs.append(block)
    return "\n\n".join(paragraphs)


CORPUS = [
    "",
    "one",
    "  padded  ",
    "a\n\nb\n\n\nc",
    "line one\nline two\nline three",
    "zna-\nczy to\nwięc\n\nnext paragraph",
    "tabs\tand  spaces \n and\r\nwindows",
    "a b  c",  # non-breaking space is whitespace too
    "\n\nleading and trailing\n\n",
    "😀 emoji 😀\nsecond",
    "a < b & c > d",
    TEXT,
]
XML_CORPUS = [
    tei("<p>a<lb/>b<lb/><lb/>c</p>"),
    tei("<p>  a  <lb/>  b  </p>"),
    tei("<p>a <hi>b</hi> <persName>c <surname>d</surname></persName> e</p>"),
    tei("<head>T</head>\n  <p>one\n   two</p>\n  <p>three<lb/>four</p>"),
    tei("<lg><l>v1</l><l>v2</l></lg><list><item>a</item><item>b</item></list>"),
    tei("<p>a <hi> b </hi> c</p>"),
    tei("<p><lb/>starts with a break<lb/></p>"),
    tei("<p>   </p><p>x</p><p/>"),
    tei("<p>x<!-- c -->y <?pi z?> w</p>"),
    tei("<p>fish &amp; chips &lt;3 <![CDATA[<raw>  & text]]></p>"),
]


@pytest.mark.parametrize("text", CORPUS)
def test_extraction_is_unchanged_for_generated_xml(text):
    xml = str(text_to_tei_xml(text))

    assert tei_xml_to_text(xml) == reference_text(xml)


@pytest.mark.parametrize("xml", XML_CORPUS)
def test_extraction_is_unchanged_for_other_xml(xml):
    assert tei_xml_to_text(xml) == reference_text(xml)


# --- marking an entity in the XML ------------------------------------------


def inner(xml: str) -> str:
    """What is inside <body> of the XML."""
    return xml[xml.index("<body>") + 6 : xml.index("</body>")]


def where(text: str, entity: str, occurrence: int = 0) -> tuple[int, int]:
    """Offsets of the n-th occurrence of `entity` in `text`, in code points."""
    index = -1
    for _ in range(occurrence + 1):
        index = text.index(entity, index + 1)
    return index, index + len(entity)


def tag(xml: str, entity: str, label: str = "persName", occurrence: int = 0) -> str:
    start, end = where(tei_xml_to_text(xml), entity, occurrence)
    return tag_text_range(xml, start, end, label, entity)


def generated(text: str) -> str:
    return str(text_to_tei_xml(text))


class TestTagTextRange:
    def test_wraps_an_entity_in_the_middle_of_a_text(self):
        xml = tag(generated("Ala ma kota. Jan Kowalski mieszka tu."), "Jan Kowalski")

        assert inner(xml) == (
            "<p>Ala ma kota. <persName>Jan Kowalski</persName> mieszka tu.</p>"
        )

    @pytest.mark.parametrize(
        ("text", "entity", "expected"),
        [
            ("Jan mieszka", "Jan", "<p><persName>Jan</persName> mieszka</p>"),
            ("mieszka Jan", "Jan", "<p>mieszka <persName>Jan</persName></p>"),
            ("Jan", "Jan", "<p><persName>Jan</persName></p>"),
            ("a\n\nJan\n\nb", "Jan", "<p>a</p><p><persName>Jan</persName></p><p>b</p>"),
        ],
    )
    def test_at_the_start_and_end_of_a_paragraph_and_alone(
        self, text, entity, expected
    ):
        assert inner(tag(generated(text), entity)) == expected

    @pytest.mark.parametrize(
        "label",
        ["persName", "placeName", "orgName", "geogName", "date", "time"],
    )
    def test_every_label_of_the_model_becomes_an_element(self, label):
        xml = tag(generated("w Paryżu"), "Paryżu", label)

        assert f"<{label}>Paryżu</{label}>" in xml

    def test_the_element_is_in_the_tei_namespace(self):
        xml = tag(generated("w Paryżu"), "Paryżu", "placeName")

        root = delb.parse_tree(xml)
        names = {n.universal_name for n in _all_tags(root)}
        assert f"{{{TEI_NAMESPACE}}}placeName" in names
        assert "xmlns=" not in inner(xml)  # not repeated on the new element

    def test_the_text_does_not_change(self):
        text = "Ala ma kota\nnia psa. Jan Kowalski\nz Paryża."

        xml = tag(generated(text), "Jan Kowalski")

        assert tei_xml_to_text(xml) == text

    def test_an_entity_can_span_a_line_break(self):
        xml = tag(generated("a Piotr\nTomicki b"), "Piotr\nTomicki")

        assert inner(xml) == "<p>a <persName>Piotr<lb/>Tomicki</persName> b</p>"

    def test_a_line_break_at_the_end_is_left_outside(self):
        # spaCy sometimes includes the newline: "Piotr\n"
        text = "a Piotr\nTomicki b"
        xml = generated(text)
        start, end = where(text, "Piotr\n")

        result = tag_text_range(xml, start, end, "persName", "Piotr\n")

        assert inner(result) == "<p>a <persName>Piotr</persName><lb/>Tomicki b</p>"

    def test_spaces_at_the_ends_are_left_outside(self):
        text = "a  Jan  b"
        xml = generated(text)  # the text is "a  Jan  b" (spaces are kept)

        start, end = where(tei_xml_to_text(xml), " Jan ")
        result = tag_text_range(xml, start, end, "persName", " Jan ")

        assert inner(result) == "<p>a  <persName>Jan</persName>  b</p>"

    def test_only_the_occurrence_at_the_offsets_is_marked(self):
        xml = tag(generated("Ala ma kota, a Ala nie"), "Ala", occurrence=1)

        assert inner(xml) == "<p>Ala ma kota, a <persName>Ala</persName> nie</p>"

    def test_offsets_count_code_points(self):
        xml = tag(generated("😀😀 Jan Kowalski 😀"), "Jan Kowalski")

        assert inner(xml) == "<p>😀😀 <persName>Jan Kowalski</persName> 😀</p>"

    def test_several_entities_can_be_marked_with_the_offsets_of_the_first_look(self):
        text = "Jan Kowalski mieszka w Paryżu od 1840 roku."
        xml = generated(text)
        entities = [
            ("Jan Kowalski", "persName"),
            ("Paryżu", "placeName"),
            ("1840 roku", "date"),
        ]
        offsets = [(*where(text, e), e, label) for e, label in entities]

        for start, end, entity, label in offsets:  # the offsets are not recomputed
            xml = tag_text_range(xml, start, end, label, entity)

        assert inner(xml) == (
            "<p><persName>Jan Kowalski</persName> mieszka w "
            "<placeName>Paryżu</placeName> od <date>1840 roku</date>.</p>"
        )
        assert tei_xml_to_text(xml) == text

    def test_next_to_each_other(self):
        xml = tag(generated("JanKowalski"), "Jan")
        xml = tag(xml, "Kowalski", "placeName")

        assert inner(xml) == (
            "<p><persName>Jan</persName><placeName>Kowalski</placeName></p>"
        )

    def test_inside_inline_markup(self):
        xml = tei("<p>a <hi>Jan Kowalski</hi> b</p>")

        assert inner(tag(xml, "Kowalski")) == (
            "<p>a <hi>Jan <persName>Kowalski</persName></hi> b</p>"
        )

    def test_around_inline_markup_that_is_fully_inside(self):
        xml = tei("<p>mieszka <hi>w Paryżu</hi> od</p>")

        assert inner(tag(xml, "mieszka w Paryżu", "placeName")) == (
            "<p><placeName>mieszka <hi>w Paryżu</hi></placeName> od</p>"
        )

    def test_whitespace_in_hand_edited_xml(self):
        xml = tei("<p>\n    Jan\n    Kowalski\n    mieszka\n  </p>")
        assert tei_xml_to_text(xml) == "Jan Kowalski mieszka"

        result = tag(xml, "Jan Kowalski")

        assert tei_xml_to_text(result) == "Jan Kowalski mieszka"
        assert "<persName>Jan\n    Kowalski</persName>" in result

    def test_marks_a_different_element_inside_an_existing_one(self):
        xml = tei("<p>w <placeName>Paryżu</placeName></p>")

        assert inner(tag(xml, "Paryżu", "persName")) == (
            "<p>w <placeName><persName>Paryżu</persName></placeName></p>"
        )

    @pytest.mark.parametrize(
        "xml",
        [
            tei("<p>w <placeName>Paryżu</placeName> mieszka</p>"),
            tei("<p>w <placeName> Paryżu </placeName></p>"),
            tei("<p><placeName>Pa<lb/>ryżu</placeName></p>"),
        ],
    )
    def test_nothing_changes_if_the_entity_is_already_marked(self, xml):
        text = tei_xml_to_text(xml)
        entity = re.search(r"Pa[\s]*ryżu", text).group(0)

        assert tag(xml, entity, "placeName") == xml

    def test_the_xml_declaration_is_kept_or_absent_like_the_input(self):
        with_declaration = generated("w Paryżu")
        without = with_declaration.split("?>", 1)[1]

        assert tag(with_declaration, "Paryżu").startswith("<?xml")
        assert not tag(without, "Paryżu").startswith("<?xml")
        assert tag(without, "Paryżu").startswith("<TEI")

    def test_the_header_and_everything_else_is_kept(self):
        xml = tag(generated("a Jan b"), "Jan")

        assert "<title>OCR text of a page</title>" in xml
        assert "<publicationStmt><p>Unpublished</p></publicationStmt>" in xml

    def test_the_result_is_well_formed_tei(self):
        xml = tag(generated(TEXT), "Lwowie", "placeName")

        assert tei_xml_to_text(xml) == TEXT.strip()
        delb.Document(xml)  # parses

    def test_tagging_twice_is_the_same_as_once(self):
        once = tag(generated("a Jan b"), "Jan")

        assert tag(once, "Jan") == once


class TestTagTextRangeErrors:
    def test_starting_inside_other_markup(self):
        xml = tei("<p>a <hi>kota</hi> i psa</p>")

        with pytest.raises(CannotTag, match=r"starts inside <hi>"):
            tag(xml, "ota i")

    def test_ending_inside_other_markup(self):
        xml = tei("<p>a <hi>kota</hi> i psa</p>")

        with pytest.raises(CannotTag, match=r"ends inside <hi>"):
            tag(xml, "a kot")

    def test_spanning_paragraphs(self):
        xml = generated("first one\n\nsecond one")

        with pytest.raises(CannotTag, match="more than one paragraph"):
            tag(xml, "one\n\nsecond")

    def test_spanning_lines_of_verse(self):
        xml = tei("<lg><l>Litwo, Ojczyzno</l><l>moja</l></lg>")

        with pytest.raises(CannotTag, match="more than one paragraph or line"):
            tag(xml, "Ojczyzno\nmoja")

    def test_spanning_list_items(self):
        xml = tei("<list><item>a b</item><item>c d</item></list>")

        with pytest.raises(CannotTag, match="more than one paragraph or line"):
            tag(xml, "b\nc")

    def test_whitespace_only(self):
        xml = generated("a b")

        with pytest.raises(CannotTag, match="no text"):
            tag_text_range(xml, 1, 2, "persName", " ")

    @pytest.mark.parametrize(
        "xml",
        [
            tei("<p>Jan<!-- c -->Kowalski</p>"),  # inside the entity
            tei("<p>Jan Kowalski<!-- c --></p>"),  # next to it
            tei("<p><?pi data?>Jan Kowalski</p>"),
        ],
    )
    def test_a_comment_or_processing_instruction_in_the_same_element(self, xml):
        entity = "JanKowalski" if "<!-- c -->Kowalski" in xml else "Jan Kowalski"

        with pytest.raises(CannotTag, match="comment or processing instruction"):
            tag(xml, entity)

    def test_a_comment_in_another_paragraph_does_not_matter(self):
        xml = tei("<p>a Jan b</p><p>c<!-- note -->d</p>")

        assert "<p>a <persName>Jan</persName> b</p><p>c<!-- note -->d</p>" in tag(
            xml, "Jan"
        )

    @pytest.mark.parametrize("label", ["", "p", "hi", "PersName", "script", "x y"])
    def test_a_label_that_is_not_an_entity_label(self, label):
        with pytest.raises(CannotTag, match="not an entity label"):
            tag_text_range(generated("Jan"), 0, 3, label, "Jan")

    def test_the_text_at_the_offsets_is_not_the_entity(self):
        with pytest.raises(TextChanged, match="page has changed"):
            tag_text_range(generated("Ala ma kota"), 0, 3, "persName", "Jan")

    @pytest.mark.parametrize(
        ("start", "end"), [(0, 99), (5, 5), (6, 2), (-1, 2), (99, 100)]
    )
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            tag_text_range(generated("Ala ma kota"), start, end, "persName", "Ala")

    @pytest.mark.parametrize(
        "xml", ["", "not xml", "<a/>", f"<TEI xmlns='{TEI_NAMESPACE}'/>"]
    )
    def test_xml_that_is_not_tei(self, xml):
        with pytest.raises(InvalidTei):
            tag_text_range(xml, 0, 1, "persName", "a")

    def test_the_xml_is_unchanged_after_an_error(self):
        # the function works on a copy: a failed attempt leaves nothing behind
        xml = tei("<p>a <hi>kota</hi> i psa</p>")

        with pytest.raises(CannotTag):
            tag(xml, "ota i")

        assert tag(xml, "psa") == xml.replace("psa", "<persName>psa</persName>")


def _all_tags(node):
    yield node
    for child in node.iterate_children():
        if isinstance(child, delb.TagNode):
            yield from _all_tags(child)


# --- random texts: tagging only adds the tags --------------------------------


def random_text(rng: random.Random) -> str:
    words = ["Jan", "Ala", "ma", "kota", "w", "Paryżu", "1840", "o-", "😀", "<", "&"]
    paragraphs = []
    for _ in range(rng.randint(1, 3)):
        lines = []
        for _ in range(rng.randint(1, 4)):
            lines.append(" ".join(rng.choice(words) for _ in range(rng.randint(1, 6))))
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


@pytest.mark.parametrize("seed", range(40))
def test_random_entities_only_add_the_tags(seed):
    rng = random.Random(seed)
    text = random_text(rng)
    xml = generated(text)
    extracted = tei_xml_to_text(xml)
    paragraph_starts = [m.start() for m in re.finditer(r"[^\n]", extracted)]
    for _ in range(5):
        # a range inside one paragraph that starts and ends on a non-space
        start = rng.choice(paragraph_starts)
        end = start + rng.randint(1, 25)
        end = min(end, len(extracted))
        chunk = extracted[start:end]
        if "\n\n" in chunk or not chunk.strip():
            continue
        label = rng.choice(["persName", "placeName", "date"])

        result = tag_text_range(xml, start, end, label, chunk)

        assert tei_xml_to_text(result) == extracted  # same text, so same offsets
        stripped = re.sub(rf"</?{label}>", "", result)
        assert stripped == xml  # nothing but the two tags was added
        assert result.count(f"<{label}>") == 1
        assert result.count(f"</{label}>") == 1
        delb.Document(result)  # still well-formed


# --- replacing a word in the XML -------------------------------------------


def replace(xml: str, before: str, after: str, occurrence: int = 0) -> str:
    start, end = where(tei_xml_to_text(xml), before, occurrence)
    return replace_text_range(xml, start, end, before, after)


class TestReplaceTextRange:
    def test_replaces_the_word_where_it_is(self):
        xml = replace(generated("Ala ma kota jey nie"), "jey", "jej")

        assert inner(xml) == "<p>Ala ma kota jej nie</p>"

    @pytest.mark.parametrize(
        ("text", "before", "after", "expected"),
        [
            ("jey ma kota", "jey", "jej", "<p>jej ma kota</p>"),
            ("ma kota jey", "jey", "jej", "<p>ma kota jej</p>"),
            ("jey", "jey", "jej", "<p>jej</p>"),
            ("a\n\njey\n\nb", "jey", "jej", "<p>a</p><p>jej</p><p>b</p>"),
            ("a\njey\nb", "jey", "jej", "<p>a<lb/>jej<lb/>b</p>"),
            ("ala", "ala", "a", "<p>a</p>"),
            ("a", "a", "bardzo długie słowo", "<p>bardzo długie słowo</p>"),
        ],
    )
    def test_at_the_ends_of_lines_and_paragraphs_and_with_other_lengths(
        self, text, before, after, expected
    ):
        assert inner(replace(generated(text), before, after)) == expected

    def test_the_text_afterwards_is_the_old_text_with_the_word_replaced(self):
        text = "Wielu Biskupów krak., że dawnieyszych kościół"
        xml = replace(generated(text), "dawnieyszych", "dawniejszych")

        assert tei_xml_to_text(xml) == text.replace("dawnieyszych", "dawniejszych")

    def test_only_the_occurrence_at_the_offsets_is_replaced(self):
        xml = replace(generated("tey i tey i tey"), "tey", "tej", occurrence=1)

        assert inner(xml) == "<p>tey i tej i tey</p>"

    def test_offsets_count_code_points(self):
        xml = replace(generated("😀😀 jey 😀"), "jey", "jej")

        assert inner(xml) == "<p>😀😀 jej 😀</p>"

    def test_a_word_next_to_punctuation(self):
        xml = replace(generated('(jey), "tey"!'), "jey", "jej")

        assert inner(xml) == '<p>(jej), "tey"!</p>'

    def test_special_characters_in_the_new_text_are_escaped(self):
        xml = replace(generated("a jey b"), "jey", "<&>")

        assert inner(xml) == "<p>a &lt;&amp;&gt; b</p>"
        assert tei_xml_to_text(xml) == "a <&> b"

    def test_the_new_text_may_have_spaces_inside(self):
        xml = replace(generated("a wcałey b"), "wcałey", "w całej")

        assert inner(xml) == "<p>a w całej b</p>"

    def test_the_element_around_the_word_stays(self):
        xml = tei("<p>a <persName>Janek</persName> b</p>")

        assert inner(replace(xml, "Janek", "Jan")) == (
            "<p>a <persName>Jan</persName> b</p>"
        )

    def test_a_word_inside_inline_markup(self):
        xml = tei("<p>a <hi>jey b</hi> c</p>")

        assert inner(replace(xml, "jey", "jej")) == "<p>a <hi>jej b</hi> c</p>"

    def test_a_word_split_by_markup_goes_into_the_first_node(self):
        xml = tei("<p>x Ja<hi>n</hi>owi y</p>")
        assert tei_xml_to_text(xml) == "x Janowi y"

        result = replace(xml, "Janowi", "Janek")

        assert inner(result) == "<p>x Janek<hi/> y</p>"
        assert tei_xml_to_text(result) == "x Janek y"

    def test_comments_are_left_where_they_are(self):
        xml = tei("<p>jey<!-- c --> tey</p>")

        assert inner(replace(xml, "jey", "jej")) == "<p>jej<!-- c --> tey</p>"

    def test_whitespace_in_hand_edited_xml(self):
        xml = tei("<p>\n   Ala   jey\n   tey\n</p>")

        result = replace(xml, "jey", "jej")

        assert tei_xml_to_text(result) == "Ala jej tey"
        assert "Ala   jej\n   tey" in result  # the layout of the source is kept

    def test_the_declaration_and_the_header_are_kept(self):
        with_declaration = generated("a jey b")
        without = with_declaration.split("?>", 1)[1]

        assert replace(with_declaration, "jey", "jej").startswith("<?xml")
        assert replace(without, "jey", "jej").startswith("<TEI")
        assert "<title>OCR text of a page</title>" in replace(without, "jey", "jej")

    def test_it_can_be_replaced_again_and_back(self):
        xml = generated("a jey b")

        there = replace(xml, "jey", "jej")

        assert replace(there, "jej", "jey") == xml

    def test_marking_an_entity_afterwards_still_works(self):
        xml = replace(generated("Jan Kowalsky mieszka"), "Kowalsky", "Kowalski")

        assert inner(tag(xml, "Jan Kowalski")) == (
            "<p><persName>Jan Kowalski</persName> mieszka</p>"
        )


class TestReplaceTextRangeErrors:
    def test_the_text_at_the_offsets_is_not_the_old_word(self):
        with pytest.raises(TextChanged, match="page has changed"):
            replace_text_range(generated("Ala ma kota"), 0, 3, "Jan", "Jas")

    @pytest.mark.parametrize(
        ("start", "end"), [(0, 99), (5, 5), (6, 2), (-1, 2), (99, 100)]
    )
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            replace_text_range(generated("Ala ma kota"), start, end, "Ala", "Ola")

    @pytest.mark.parametrize(
        ("text", "start", "end"),
        [
            ("Ala ma kota", 0, 6),  # "Ala ma": a space inside
            ("Ala\nma kota", 0, 6),  # a line break inside
            ("x Ala y", 1, 5),  # " Ala": a space at the start
            ("Ala y", 0, 4),  # "Ala ": a space at the end
        ],
    )
    def test_the_old_text_must_not_have_spaces(self, text, start, end):
        xml = generated(text)
        before = tei_xml_to_text(xml)[start:end]

        with pytest.raises(CannotReplace, match="spaces or line breaks"):
            replace_text_range(xml, start, end, before, "Ola")

    def test_a_token_is_removed_with_one_of_the_spaces_around_it(self):
        xml = generated("Ala $ ma kota")
        start, end = where(tei_xml_to_text(xml), "$")

        changed = replace_text_range(xml, start, end, "$", "")

        assert "<p>Ala ma kota</p>" in changed  # not "Ala  ma kota"
        assert tei_xml_to_text(changed) == "Ala ma kota"

    def test_a_token_glued_to_a_word_is_removed_alone(self):
        xml = generated("Ala (|}ma kota")
        start, end = where(tei_xml_to_text(xml), "(|}")

        changed = replace_text_range(xml, start, end, "(|}", "")

        assert tei_xml_to_text(changed) == "Ala ma kota"

    def test_a_removed_token_keeps_the_line_break_next_to_it(self):
        xml = generated("Ala ma\n$\nkota")
        start, end = where(tei_xml_to_text(xml), "$")

        changed = replace_text_range(xml, start, end, "$", "")

        assert tei_xml_to_text(changed) == "Ala ma\n\nkota"

    @pytest.mark.parametrize(
        ("after", "message"),
        [
            (" jej", "start or end with a space"),
            ("jej ", "start or end with a space"),
            ("je\nj", "one line"),
            ("je\tj", "one line"),
            ("je\x00j", "one line"),
            ("jey", "same as the old"),
        ],
    )
    def test_the_new_text_must_be_acceptable(self, after, message):
        xml = generated("a jey b")
        start, end = where(tei_xml_to_text(xml), "jey")

        with pytest.raises(CannotReplace, match=message):
            replace_text_range(xml, start, end, "jey", after)

    @pytest.mark.parametrize(
        "xml", ["", "not xml", "<a/>", f"<TEI xmlns='{TEI_NAMESPACE}'/>"]
    )
    def test_xml_that_is_not_tei(self, xml):
        with pytest.raises(InvalidTei):
            replace_text_range(xml, 0, 1, "a", "b")

    def test_nothing_is_changed_after_an_error(self):
        xml = generated("a jey b")

        with pytest.raises(CannotReplace):
            replace_text_range(xml, 2, 5, "jey", " jej")

        assert replace(xml, "jey", "jej") == xml.replace("jey", "jej")


@pytest.mark.parametrize("seed", range(40))
def test_random_words_replaced_only_change_the_word(seed):
    rng = random.Random(seed)
    text = random_text(rng)
    xml = generated(text)
    extracted = tei_xml_to_text(xml)
    words = [m for m in re.finditer(r"\S+", extracted)]
    for _ in range(5):
        match = rng.choice(words)
        before = match.group()
        after = rng.choice(["zażółć", "x", "ab cd", "Jan", "😀😀", "a<b&c"])
        if after == before:
            continue

        result = replace_text_range(xml, match.start(), match.end(), before, after)

        expected = extracted[: match.start()] + after + extracted[match.end() :]
        assert tei_xml_to_text(result) == expected
        delb.Document(result)  # still well-formed


# --- marking the sections of a page -----------------------------------------


def mark(
    xml: str, selection: str, section: str, occurrence: int = 0, n: str | None = None
) -> str:
    start, end = where(tei_xml_to_text(xml), selection, occurrence)
    return mark_section(xml, start, end, section, selection, n)


def text_without_spaces(xml: str) -> str:
    return re.sub(r"\s+", "", tei_xml_to_text(xml))


class TestMarkSectionFw:
    """Header, footer and page number: <fw> around the text itself."""

    @pytest.mark.parametrize(
        ("section", "markup"),
        [
            ("header", '<fw type="header">'),
            ("footer", '<fw type="footer">'),
            ("page_number", '<fw type="pageNum">'),
        ],
    )
    def test_each_is_a_forme_work_element_of_its_type(self, section, markup):
        xml = mark(generated("Ala ma 84 kota"), "84", section)

        assert inner(xml) == f"<p>Ala ma {markup}84</fw> kota</p>"

    def test_a_header_that_is_a_paragraph_of_its_own(self):
        xml = mark(generated("84 KATEDRA\n\ncle przekazał"), "84 KATEDRA", "header")

        assert (
            inner(xml) == '<p><fw type="header">84 KATEDRA</fw></p><p>cle przekazał</p>'
        )

    def test_a_footer_after_a_line_break(self):
        xml = mark(generated("text\nstopka 84"), "stopka 84", "footer")

        assert inner(xml) == '<p>text<lb/><fw type="footer">stopka 84</fw></p>'

    def test_it_can_span_a_line_break(self):
        xml = mark(generated("a head\nline b"), "head\nline", "header")

        assert inner(xml) == '<p>a <fw type="header">head<lb/>line</fw> b</p>'

    def test_the_text_does_not_change(self):
        text = "84 KATEDRA\n\ncle przekazał\nniami"

        for section in ("header", "footer", "page_number"):
            assert tei_xml_to_text(mark(generated(text), "84", section)) == text

    def test_a_page_number_inside_a_header(self):
        xml = mark(generated("84 KATEDRA"), "KATEDRA", "header")  # not this
        xml = mark(generated("84 KATEDRA"), "84 KATEDRA", "header")

        xml = mark(xml, "84", "page_number")

        assert inner(xml) == (
            '<p><fw type="header"><fw type="pageNum">84</fw> KATEDRA</fw></p>'
        )

    def test_spaces_and_line_breaks_around_the_selection_are_left_out(self):
        xml = tei("<p>a<lb/>84 <lb/>b</p>")
        text = tei_xml_to_text(xml)
        assert text == "a\n84\nb"
        start, end = where(text, "\n84\n")

        result = mark_section(xml, start, end, "page_number", "\n84\n")

        # the line breaks (and the space after the number) stay outside the element
        assert inner(result) == '<p>a<lb/><fw type="pageNum">84</fw> <lb/>b</p>'

    def test_marking_the_same_again_changes_nothing(self):
        once = mark(generated("a 84 b"), "84", "page_number")

        assert mark(once, "84", "page_number") == once

    def test_a_different_section_for_the_same_text_goes_inside_the_element(self):
        once = mark(generated("a 84 b"), "84", "page_number")

        twice = mark(once, "84", "footer")

        # like an entity: the selection is the content of the pageNum element, so
        # the new element goes around that content, inside it
        assert inner(twice) == (
            '<p>a <fw type="pageNum"><fw type="footer">84</fw></fw> b</p>'
        )

    def test_only_the_occurrence_at_the_offsets(self):
        xml = mark(generated("84 a 84"), "84", "page_number", occurrence=1)

        assert inner(xml) == '<p>84 a <fw type="pageNum">84</fw></p>'

    def test_offsets_count_code_points(self):
        xml = mark(generated("😀 84 😀"), "84", "page_number")

        assert inner(xml) == '<p>😀 <fw type="pageNum">84</fw> 😀</p>'

    def test_it_cannot_span_paragraphs(self):
        xml = generated("one\n\ntwo")

        with pytest.raises(CannotTag, match="more than one paragraph"):
            mark(xml, "one\n\ntwo", "header")

    def test_it_cannot_start_inside_other_markup(self):
        xml = tei("<p>a <hi>kota</hi> i psa</p>")

        with pytest.raises(CannotTag, match="starts inside <hi>"):
            mark(xml, "ota i", "header")


class TestMarkSectionNotes:
    """A note is <note> around the text; a note group is a <noteGrp> of notes."""

    def test_a_note(self):
        xml = mark(generated("Ala ma kota 1 przypis"), "1 przypis", "note")

        assert inner(xml) == "<p>Ala ma kota <note>1 przypis</note></p>"

    def test_a_note_with_its_number(self):
        xml = mark(generated("Ala 1 przypis"), "1 przypis", "note", n="1")

        assert inner(xml) == '<p>Ala <note n="1">1 przypis</note></p>'

    def test_a_note_can_span_a_line_break_and_keeps_the_text(self):
        original = generated("a\nprzypis jeden\ndalej b")
        xml = mark(original, "przypis jeden\ndalej", "note", n="2")

        assert tei_xml_to_text(xml) == tei_xml_to_text(original)
        assert '<note n="2">przypis jeden<lb/>dalej</note>' in xml

    def test_a_number_is_only_for_a_note(self):
        with pytest.raises(CannotTag, match="number"):
            mark(generated("Ala"), "Ala", "header", n="1")

    def test_marking_the_same_note_again_changes_nothing(self):
        once = mark(generated("x 1 przypis"), "1 przypis", "note", n="1")

        assert mark(once, "1 przypis", "note", n="1") == once

    def notes(self):
        xml = generated("tekst\n1 pierwszy\n2 drugi\nkoniec")
        xml = mark(xml, "1 pierwszy", "note", n="1")
        return mark(xml, "2 drugi", "note", n="2")

    def test_a_group_of_notes_on_separate_lines(self):
        before = self.notes()

        xml = mark(before, "1 pierwszy\n2 drugi", "note_group")

        assert (
            '<noteGrp><note n="1">1 pierwszy</note>\n<note n="2">2 drugi</note>'
            "</noteGrp>"
        ) in xml  # the <lb/> between them, which a <noteGrp> may not hold, is a newline
        assert tei_xml_to_text(xml) == tei_xml_to_text(before)

    def test_a_group_of_notes_in_one_line(self):
        xml = mark(generated("a 1 jeden 2 dwa b"), "1 jeden", "note", n="1")
        xml = mark(xml, "2 dwa", "note", n="2")

        grouped = mark(xml, "1 jeden 2 dwa", "note_group")

        assert inner(grouped) == (
            '<p>a <noteGrp><note n="1">1 jeden</note> <note n="2">2 dwa</note>'
            "</noteGrp> b</p>"
        )
        assert tei_xml_to_text(grouped) == tei_xml_to_text(xml)

    def test_a_group_of_one_note(self):
        xml = mark(generated("a 1 jeden b"), "1 jeden", "note", n="1")

        assert inner(mark(xml, "1 jeden", "note_group")) == (
            '<p>a <noteGrp><note n="1">1 jeden</note></noteGrp> b</p>'
        )

    @pytest.mark.parametrize("selection", ["a 1 jeden", "1 jeden 2", "jeden"])
    def test_a_group_is_refused_for_what_is_not_only_notes(self, selection):
        xml = mark(generated("a 1 jeden 2 dwa b"), "1 jeden", "note", n="1")
        xml = mark(xml, "2 dwa", "note", n="2")

        with pytest.raises(CannotTag, match="only notes"):
            mark(xml, selection, "note_group")

    def test_a_group_is_refused_for_text_that_is_not_a_note(self):
        with pytest.raises(CannotTag, match="only notes"):
            mark(generated("Ala ma kota"), "Ala", "note_group")

    def test_marking_the_same_group_again_changes_nothing(self):
        xml = mark(generated("a 1 jeden 2 dwa b"), "1 jeden", "note", n="1")
        xml = mark(xml, "2 dwa", "note", n="2")
        once = mark(xml, "1 jeden 2 dwa", "note_group")

        assert mark(once, "1 jeden 2 dwa", "note_group") == once

    def test_the_result_is_well_formed(self):
        xml = mark(generated("a 1 jeden 2 dwa b"), "1 jeden", "note", n="1")
        xml = mark(xml, "2 dwa", "note", n="2")

        delb.Document(mark(xml, "1 jeden 2 dwa", "note_group"))


class TestMarkSectionToc:
    """The table of contents: <div type="toc"> around whole paragraphs."""

    def test_a_paragraph_in_the_middle(self):
        xml = mark(generated("a\n\nI. Alfa\n\nb\n\nc"), "I. Alfa", "toc")

        assert inner(xml) == (
            '<p>a</p><div type="toc"><p>I. Alfa</p></div><div><p>b</p><p>c</p></div>'
        )

    def test_several_paragraphs(self):
        xml = mark(
            generated("a\n\nI. Alfa\n\nII. Beta\n\nb"), "I. Alfa\n\nII. Beta", "toc"
        )

        assert inner(xml) == (
            '<p>a</p><div type="toc"><p>I. Alfa</p><p>II. Beta</p></div>'
            "<div><p>b</p></div>"
        )

    def test_the_last_paragraph_needs_no_div_after_it(self):
        xml = mark(generated("a\n\nb\n\nI. Alfa"), "I. Alfa", "toc")

        assert inner(xml) == '<p>a</p><p>b</p><div type="toc"><p>I. Alfa</p></div>'

    def test_the_first_paragraph(self):
        xml = mark(generated("I. Alfa\n\na\n\nb"), "I. Alfa", "toc")

        assert (
            inner(xml)
            == '<div type="toc"><p>I. Alfa</p></div><div><p>a</p><p>b</p></div>'
        )

    def test_the_only_paragraph(self):
        xml = mark(generated("I. Alfa\nII. Beta"), "I. Alfa\nII. Beta", "toc")

        assert inner(xml) == '<div type="toc"><p>I. Alfa<lb/>II. Beta</p></div>'

    def test_lines_in_the_middle_of_a_paragraph_split_it_in_three(self):
        xml = mark(generated("a\nI. Alfa\nII. Beta\nz"), "I. Alfa\nII. Beta", "toc")

        # the line break that starts a line goes with that line
        assert inner(xml) == (
            '<p>a</p><div type="toc"><p><lb/>I. Alfa<lb/>II. Beta</p></div>'
            "<div><p><lb/>z</p></div>"
        )

    def test_lines_at_the_start_of_a_paragraph_split_it_in_two(self):
        xml = mark(generated("I. Alfa\nII. Beta\nz"), "I. Alfa\nII. Beta", "toc")

        assert inner(xml) == (
            '<div type="toc"><p>I. Alfa<lb/>II. Beta</p></div><div><p><lb/>z</p></div>'
        )

    def test_lines_at_the_end_of_a_paragraph_split_it_in_two(self):
        xml = mark(generated("a\nI. Alfa\nII. Beta"), "I. Alfa\nII. Beta", "toc")

        assert inner(xml) == (
            '<p>a</p><div type="toc"><p><lb/>I. Alfa<lb/>II. Beta</p></div>'
        )

    def test_part_of_a_line_splits_the_paragraph_there(self):
        xml = mark(generated("a I. Alfa b"), "I. Alfa", "toc")

        assert (
            inner(xml)
            == '<p>a </p><div type="toc"><p>I. Alfa</p></div><div><p> b</p></div>'
        )

    def test_from_the_middle_of_one_paragraph_to_the_middle_of_another(self):
        xml = mark(generated("a b\nc d\n\ne f\ng h\n\ni"), "c d\n\ne f", "toc")

        assert inner(xml) == (
            '<p>a b</p><div type="toc"><p><lb/>c d</p><p>e f</p></div>'
            "<div><p><lb/>g h</p><p>i</p></div>"
        )

    def test_the_text_has_a_blank_line_where_a_paragraph_was_split(self):
        text = "a\nI. Alfa\nII. Beta\nz"

        xml = mark(generated(text), "I. Alfa\nII. Beta", "toc")

        assert tei_xml_to_text(xml) == "a\n\nI. Alfa\nII. Beta\n\nz"
        assert text_without_spaces(xml) == re.sub(r"\s+", "", text)

    def test_the_text_does_not_change_when_no_paragraph_is_split(self):
        text = "a\n\nI. Alfa\n\nb"

        assert tei_xml_to_text(mark(generated(text), "I. Alfa", "toc")) == text

    def test_spaces_around_the_selection_are_ignored(self):
        xml = generated("a\n\n I. Alfa \n\nb")
        text = tei_xml_to_text(xml)
        start, end = where(text, "I. Alfa")

        trimmed = mark_section(xml, start, end, "toc", "I. Alfa")
        padded = mark_section(xml, start - 1, end + 1, "toc", text[start - 1 : end + 1])

        assert inner(trimmed) == inner(padded)

    def test_a_paragraph_with_inline_markup_is_split_before_the_markup(self):
        xml = tei("<p>a <hi>b</hi> c</p>")

        result = mark(xml, "b c", "toc")

        assert inner(result) == ('<p>a </p><div type="toc"><p><hi>b</hi> c</p></div>')

    def test_it_cannot_start_inside_inline_markup(self):
        xml = tei("<p>a <hi>bcd</hi> e</p>")

        with pytest.raises(CannotTag, match="starts inside <hi>"):
            mark(xml, "cd e", "toc")

    def test_it_cannot_end_inside_inline_markup(self):
        xml = tei("<p>a <hi>bcd</hi> e</p>")

        with pytest.raises(CannotTag, match="ends inside <hi>"):
            mark(xml, "a bc", "toc")

    def test_the_same_again_changes_nothing(self):
        once = mark(generated("a\n\nI. Alfa\n\nb"), "I. Alfa", "toc")

        assert mark(once, "I. Alfa", "toc") == once

    def test_marking_part_of_a_toc_again_nests_or_splits_inside_it(self):
        once = mark(
            generated("a\n\nI. Alfa\nII. Beta\n\nb"), "I. Alfa\nII. Beta", "toc"
        )

        twice = mark(once, "II. Beta", "toc")

        # the second line becomes a toc of its own, inside the first
        assert 'type="toc"' in inner(twice)
        assert tei_xml_to_text(twice).count("II. Beta") == 1

    def test_a_second_toc_further_on(self):
        xml = generated("a\n\nI. Alfa\n\nb\n\nII. Beta\n\nc")
        first = mark(xml, "I. Alfa", "toc")

        second = mark(first, "II. Beta", "toc")

        assert inner(second) == (
            '<p>a</p><div type="toc"><p>I. Alfa</p></div>'
            '<div><p>b</p><div type="toc"><p>II. Beta</p></div>'
            "<div><p>c</p></div></div>"
        )

    def test_the_xml_declaration_is_kept_or_absent_like_the_input(self):
        with_declaration = generated("a\n\nI. Alfa")
        without = with_declaration.split("?>", 1)[1]

        assert mark(with_declaration, "I. Alfa", "toc").startswith("<?xml")
        assert mark(without, "I. Alfa", "toc").startswith("<TEI")

    def test_the_header_is_kept(self):
        xml = mark(generated("a\n\nI. Alfa"), "I. Alfa", "toc")

        assert "<title>OCR text of a page</title>" in xml

    def test_hand_edited_xml_with_indentation(self):
        xml = tei("\n  <p>a</p>\n  <p>I. Alfa</p>\n  <p>b</p>\n")

        result = mark(xml, "I. Alfa", "toc")

        assert re.sub(r">\s+<", "><", inner(result).strip()) == (
            '<p>a</p><div type="toc"><p>I. Alfa</p></div><div><p>b</p></div>'
        )

    @pytest.mark.parametrize(
        ("body", "selection"),
        [
            ("<head>Spis</head><p>x</p>", "Spis"),
            ("<ab>I. Alfa</ab>", "I. Alfa"),
        ],
    )
    def test_text_that_is_not_in_a_paragraph_is_refused(self, body, selection):
        with pytest.raises(CannotTag, match="not in a paragraph"):
            mark(tei(body), selection, "toc")

    def test_paragraphs_in_different_divisions_are_refused(self):
        xml = tei("<div><p>a</p></div><div><p>b</p></div>")

        with pytest.raises(CannotTag, match="different divisions"):
            mark(xml, "a\n\nb", "toc")

    def test_a_paragraph_with_a_comment_that_has_to_be_split_is_refused(self):
        xml = tei("<p>a<!-- c --> I. Alfa</p>")

        with pytest.raises(CannotTag, match="comment"):
            mark(xml, "I. Alfa", "toc")

    def test_a_comment_in_a_paragraph_that_is_not_split_is_fine(self):
        xml = tei("<p>x</p><p>I. Alfa<!-- c --></p><p>y</p>")

        result = mark(xml, "I. Alfa", "toc")

        assert '<div type="toc"><p>I. Alfa<!-- c --></p></div>' in result


class TestMarkSectionErrors:
    def test_the_text_at_the_offsets_is_not_the_selection(self):
        with pytest.raises(TextChanged, match="page has changed"):
            mark_section(generated("Ala ma kota"), 0, 3, "header", "Jan")

    @pytest.mark.parametrize(
        ("start", "end"), [(0, 99), (5, 5), (6, 2), (-1, 2), (99, 100)]
    )
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            mark_section(generated("Ala ma kota"), start, end, "toc", "Ala")

    def test_a_selection_of_only_spaces(self):
        with pytest.raises(CannotTag, match="no text"):
            mark_section(generated("a b"), 1, 2, "header", " ")

    @pytest.mark.parametrize("section", ["", "page", "H", "div", "persName", "x y"])
    def test_a_section_that_is_not_one(self, section):
        with pytest.raises(CannotTag, match="not a section"):
            mark_section(generated("Ala"), 0, 3, section, "Ala")

    @pytest.mark.parametrize(
        "xml", ["", "not xml", "<a/>", f"<TEI xmlns='{TEI_NAMESPACE}'/>"]
    )
    def test_xml_that_is_not_tei(self, xml):
        with pytest.raises(InvalidTei):
            mark_section(xml, 0, 1, "header", "a")

    def test_nothing_is_changed_after_an_error(self):
        xml = generated("one\n\ntwo")

        with pytest.raises(CannotTag):
            mark(xml, "one\n\ntwo", "header")

        assert mark(xml, "two", "header") == xml.replace(
            "two", '<fw type="header">two</fw>'
        )

    def test_the_entity_errors_still_say_entity(self):
        xml = generated("one\n\ntwo")
        start, end = where(tei_xml_to_text(xml), "one\n\ntwo")

        with pytest.raises(CannotTag, match="The entity spans"):
            tag_text_range(xml, start, end, "persName", "one\n\ntwo")


def structure_is_valid_tei(xml: str) -> bool:
    """TEI allows nothing but <div> (and <fw>) after a <div> in the same element."""
    root = delb.parse_tree(xml)

    def check(node) -> bool:
        seen_div = False
        for child in node.iterate_children():
            if not isinstance(child, delb.TagNode):
                continue
            if child.local_name == "div":
                seen_div = True
            elif seen_div and child.local_name not in ("fw",):
                return False
            if not check(child):
                return False
        return True

    return check(root)


@pytest.mark.parametrize("seed", range(60))
def test_random_sections_keep_the_text_and_a_valid_structure(seed):
    rng = random.Random(seed)
    text = random_text(rng)
    xml = generated(text)
    for _ in range(rng.randint(1, 4)):
        extracted = tei_xml_to_text(xml)
        start = rng.choice([m.start() for m in re.finditer(r"\S", extracted)])
        end = min(len(extracted), start + rng.randint(1, 50))
        selection = extracted[start:end]
        section = rng.choice(["header", "footer", "page_number", "toc", "toc"])
        try:
            result = mark_section(xml, start, end, section, selection)
        except CannotTag:
            continue

        assert text_without_spaces(result) == text_without_spaces(
            xml
        )  # only blank lines
        assert structure_is_valid_tei(result)
        delb.Document(result)  # well-formed
        if section != "toc":
            assert tei_xml_to_text(result) == extracted  # the text is the same
        xml = result


class TestInsertTextAt:
    TEXT = "Ala ma kota\nnoc"

    def insert(self, position, token="XX", text=TEXT):
        return tei_xml_to_text(insert_text_at(generated(text), position, token))

    @pytest.mark.parametrize(
        ("position", "expected"),
        [
            (0, "XX Ala ma kota\nnoc"),  # the start of the text
            (4, "Ala XX ma kota\nnoc"),  # between words: one space on each side
            (3, "Ala XX ma kota\nnoc"),  # right after a word
            (11, "Ala ma kota XX\nnoc"),  # the end of a line
            (12, "Ala ma kota\nXX noc"),  # the start of the next line
            (15, "Ala ma kota\nnoc XX"),  # the end of the text
            (1, "A XX la ma kota\nnoc"),  # in a word: it is cut in two
        ],
    )
    def test_puts_the_token_at_the_place_as_a_token_of_its_own(
        self, position, expected
    ):
        assert self.insert(position) == expected

    def test_the_text_before_and_after_is_the_same(self):
        text = tei_xml_to_text(insert_text_at(generated(self.TEXT), 4, "XX"))

        assert text.replace(" XX", "", 1) == self.TEXT

    def test_the_markup_around_stays(self):
        xml = tag_text_range(
            generated("Jan Kowalski tu"), 0, 12, "persName", "Jan Kowalski"
        )

        result = insert_text_at(xml, 4, "X")

        assert "<persName>Jan X Kowalski</persName>" in result

    def test_a_token_after_the_last_word_of_an_entity_goes_into_it(self):
        xml = tag_text_range(
            generated("Jan Kowalski tu"), 0, 12, "persName", "Jan Kowalski"
        )

        result = insert_text_at(xml, 12, "X")

        assert "<persName>Jan Kowalski X</persName>" in result

    def test_after_a_line_break_in_the_xml(self):
        xml = generated("Ala\nma")

        assert tei_xml_to_text(insert_text_at(xml, 4, "X")) == "Ala\nX ma"

    def test_counts_code_points_after_emoji(self):
        text = "😀 Ala ma"

        assert self.insert(6, text=text) == "😀 Ala XX ma"

    def test_not_on_the_empty_line_between_paragraphs(self):
        with pytest.raises(CannotInsert, match="between two paragraphs"):
            self.insert(4, text="Ala\n\nma")

    def test_the_start_of_the_next_paragraph_is_a_place(self):
        assert self.insert(5, text="Ala\n\nma") == "Ala\n\nXX ma"

    @pytest.mark.parametrize("position", [-1, 16, 100])
    def test_a_position_outside_the_text(self, position):
        with pytest.raises(CannotInsert, match="no place"):
            self.insert(position)

    def test_a_token_may_be_several_words(self):
        assert self.insert(4, "LIST SZÓSTY") == "Ala LIST SZÓSTY ma kota\nnoc"

    def test_a_run_of_spaces_in_a_token_is_one_space(self):
        assert self.insert(4, "LIST   SZÓSTY") == "Ala LIST SZÓSTY ma kota\nnoc"

    @pytest.mark.parametrize(
        "token", ["", " ", " a", "a ", "a\nb", "a\tb", "a\x00", "a\u00a0b"]
    )
    def test_a_token_must_be_acceptable(self, token):
        with pytest.raises(CannotInsert):
            self.insert(4, token)

    def test_rejects_xml_that_is_not_tei(self):
        with pytest.raises(InvalidTei):
            insert_text_at("<a/>", 0, "X")

    def test_an_empty_page_takes_the_token_as_its_first_line(self):
        result = insert_text_at(generated(""), 0, "LIST SZÓSTY")

        assert tei_xml_to_text(result) == "LIST SZÓSTY"
        assert "<p>LIST SZÓSTY</p>" in result

    def test_a_page_of_whitespace_only_takes_the_token_too(self):
        xml = generated("").replace("<p/>", "<p>  \n </p>")

        assert tei_xml_to_text(insert_text_at(xml, 0, "X")) == "X"

    def test_an_empty_page_without_a_paragraph_gets_one(self):
        xml = generated("").replace("<body><p/></body>", "<body/>")

        result = insert_text_at(xml, 0, "X")

        assert "<body><p>X</p></body>" in result

    def test_an_empty_page_without_a_body_gets_one(self):
        xml = generated("").replace("<body><p/></body>", "")

        result = insert_text_at(xml, 0, "X")

        assert "<body><p>X</p></body>" in result
        assert tei_xml_to_text(result) == "X"

    def test_the_token_of_an_empty_page_is_still_checked(self):
        with pytest.raises(CannotInsert):
            insert_text_at(generated(""), 0, " X")

    def test_a_position_after_the_text_of_an_empty_page_is_refused(self):
        with pytest.raises(CannotInsert):
            insert_text_at(generated(""), 1, "X")


class TestJoinTextRange:
    TEXT = "Ala jest abso lutely tu\nnoc"

    def join(self, start=9, end=20, text="abso lutely", xml=None, source=TEXT):
        return tei_xml_to_text(
            join_text_range(xml or generated(source), start, end, text)
        )

    def test_removes_the_space_between_two_tokens(self):
        assert self.join() == "Ala jest absolutely tu\nnoc"

    def test_joins_more_than_two_tokens(self):
        assert self.join(4, 23, "jest abso lutely tu") == "Ala jestabsolutelytu\nnoc"

    def test_the_rest_of_the_text_is_the_same(self):
        assert self.join().replace("absolutely", "abso lutely") == self.TEXT

    def test_the_whole_run_of_whitespace_goes(self):
        xml = generated(self.TEXT).replace("abso lutely", "abso \n   lutely")

        assert self.join(xml=xml) == "Ala jest absolutely tu\nnoc"

    def test_the_markup_around_stays(self):
        xml = tag_text_range(generated(self.TEXT), 4, 8, "persName", "jest")

        result = join_text_range(xml, 9, 20, "abso lutely")

        assert "<persName>jest</persName>" in result
        assert "absolutely" in result

    def test_a_space_between_two_elements_goes(self):
        xml = generated("Ala ma kota")
        xml = tag_text_range(xml, 0, 3, "persName", "Ala")
        xml = tag_text_range(xml, 4, 6, "placeName", "ma")

        result = join_text_range(xml, 0, 6, "Ala ma")

        assert tei_xml_to_text(result) == "Alama kota"
        assert "<persName>Ala</persName><placeName>ma</placeName>" in result

    def test_counts_code_points_after_emoji(self):
        assert self.join(11, 22, "abso lutely", source="😀 Ala jest abso lutely") == (
            "😀 Ala jest absolutely"
        )

    def test_the_text_must_be_what_is_there(self):
        with pytest.raises(TextChanged):
            self.join(text="abso lutelz")

    @pytest.mark.parametrize("start, end", [(-1, 20), (9, 99), (20, 9)])
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            self.join(start, end)

    def test_tokens_on_different_lines_are_not_joined(self):
        with pytest.raises(CannotJoin):
            self.join(21, 27, "tu\nnoc")

    @pytest.mark.parametrize(
        "text", ["abso", "abso  lutely", " abso lutely", "abso lutely ", "a\tb"]
    )
    def test_the_text_must_be_tokens_with_single_spaces(self, text):
        with pytest.raises(CannotJoin):
            join_text_range(generated(self.TEXT), 9, 20, text)

    def test_rejects_xml_that_is_not_tei(self):
        with pytest.raises(InvalidTei):
            join_text_range("<a/>", 0, 3, "a b")


class TestSplitLineAt:
    TEXT = "Ala ma kota\nA kot ma Ale\n\nNowy akapit"

    def split(self, text, source=TEXT, xml=None, start=None):
        if start is None:
            start = source.index(text)
        return split_line_at(xml or generated(source), start, start + len(text), text)

    def test_the_space_becomes_a_line_break(self):
        result = self.split("ma kota")

        assert tei_xml_to_text(result) == "Ala ma\nkota\nA kot ma Ale\n\nNowy akapit"
        assert "<p>Ala ma<lb/>kota<lb/>A kot ma Ale</p>" in result

    def test_the_rest_of_the_text_is_the_same(self):
        result = tei_xml_to_text(self.split("kot ma"))

        assert result == "Ala ma kota\nA kot\nma Ale\n\nNowy akapit"
        assert result.replace("kot\nma", "kot ma") == self.TEXT

    def test_a_token_put_at_the_end_of_a_line_can_be_given_a_line_of_its_own(self):
        with_token = insert_text_at(generated("Ala ma kota"), 11, "XXX")

        result = split_line_at(with_token, 7, 15, "kota XXX")

        assert tei_xml_to_text(result) == "Ala ma kota\nXXX"

    def test_the_whole_run_of_whitespace_goes(self):
        xml = generated("Ala ma kota").replace("Ala ma", "Ala \n   ma")

        result = split_line_at(xml, 0, 6, "Ala ma")

        assert tei_xml_to_text(result) == "Ala\nma kota"
        assert "<p>Ala<lb/>ma kota</p>" in result

    def test_the_markup_around_stays(self):
        xml = tag_text_range(
            generated("Ala Jan Kowalski ma"), 4, 16, "persName", "Jan Kowalski"
        )

        result = self.split("Jan Kowalski", "Ala Jan Kowalski ma", xml)

        assert "<persName>Jan<lb/>Kowalski</persName>" in result
        assert tei_xml_to_text(result) == "Ala Jan\nKowalski ma"

    def test_a_space_between_two_elements_becomes_the_line_break(self):
        xml = generated("Ala ma kota")
        xml = tag_text_range(xml, 0, 3, "persName", "Ala")
        xml = tag_text_range(xml, 4, 6, "placeName", "ma")

        result = split_line_at(xml, 0, 6, "Ala ma")

        assert "<persName>Ala</persName><lb/><placeName>ma</placeName>" in result

    def test_a_hyphen_before_the_break_makes_it_a_break_inside_a_word(self):
        result = self.split("kro- kodyl", "Ala kro- kodyl")

        assert '<lb break="no"/>' in result
        assert tei_xml_to_text(result) == "Ala kro-\nkodyl"

    def test_a_line_break_without_a_hyphen_is_a_plain_one(self):
        assert '<lb break="no"/>' not in self.split("ma kota")

    def test_counts_code_points_after_emoji(self):
        source = "😀 Ala ma kota"

        result = self.split("ma kota", source)

        assert tei_xml_to_text(result) == "😀 Ala ma\nkota"

    def test_the_text_must_be_what_is_there(self):
        with pytest.raises(TextChanged):
            split_line_at(generated(self.TEXT), 4, 11, "ma kotu")

    @pytest.mark.parametrize("start, end", [(-1, 7), (4, 99), (7, 4)])
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            split_line_at(generated(self.TEXT), start, end, "ma kota")

    @pytest.mark.parametrize(
        "text",
        [
            "ma",  # one token
            "ma  kota",  # two spaces
            " ma kota",  # a space at the start
            "ma kota ",  # a space at the end
            "ma kota tu",  # three tokens
            "kota\nA",  # a line break is not a space
            "ma\tkota",
        ],
    )
    def test_the_text_must_be_two_tokens_with_one_space(self, text):
        with pytest.raises(CannotSplit):
            split_line_at(generated(self.TEXT), 4, 4 + len(text), text)

    def test_rejects_xml_that_is_not_tei(self):
        with pytest.raises(InvalidTei):
            split_line_at("<a/>", 0, 3, "a b")


class TestJoinLinesAt:
    TEXT = "Ala ma kota\nA kot ma Ale\n\nNowy akapit"

    def join(self, text="kota\nA", source=TEXT, xml=None):
        start = source.index(text)
        return join_lines_at(xml or generated(source), start, start + len(text), text)

    def test_the_line_break_becomes_a_space(self):
        result = self.join()

        assert tei_xml_to_text(result) == "Ala ma kota A kot ma Ale\n\nNowy akapit"
        assert "<p>Ala ma kota A kot ma Ale</p>" in result

    def test_it_undoes_a_split(self):
        split = split_line_at(generated("Ala ma kota"), 4, 11, "ma kota")
        assert tei_xml_to_text(split) == "Ala ma\nkota"

        joined = join_lines_at(split, 4, 11, "ma\nkota")

        assert tei_xml_to_text(joined) == "Ala ma kota"

    def test_the_markup_around_stays(self):
        xml = split_line_at(
            tag_text_range(
                generated("Ala Jan Kowalski ma"), 4, 16, "persName", "Jan Kowalski"
            ),
            4,
            16,
            "Jan Kowalski",
        )
        assert "<persName>Jan<lb/>Kowalski</persName>" in xml

        result = join_lines_at(xml, 4, 16, "Jan\nKowalski")

        assert "<persName>Jan Kowalski</persName>" in result
        assert "<lb" not in result

    def test_a_break_between_two_elements_becomes_a_space_between_them(self):
        xml = generated("Ala ma kota")
        xml = tag_text_range(xml, 0, 3, "persName", "Ala")
        xml = tag_text_range(xml, 4, 6, "placeName", "ma")
        xml = split_line_at(xml, 0, 6, "Ala ma")

        result = join_lines_at(xml, 0, 6, "Ala\nma")

        assert "<persName>Ala</persName> <placeName>ma</placeName>" in result

    def test_a_break_inside_a_word_becomes_a_space_too(self):
        xml = split_line_at(generated("Ala kro- kodyl"), 4, 14, "kro- kodyl")
        assert '<lb break="no"/>' in xml

        result = join_lines_at(xml, 4, 14, "kro-\nkodyl")

        assert tei_xml_to_text(result) == "Ala kro- kodyl"
        assert "<lb" not in result

    def test_only_the_chosen_line_break_goes(self):
        source = "a b\nc d\ne f"

        result = self.join("d\ne", source)

        assert tei_xml_to_text(result) == "a b\nc d e f"

    def test_counts_code_points_after_emoji(self):
        source = "😀 Ala\nma"

        assert tei_xml_to_text(self.join("Ala\nma", source)) == "😀 Ala ma"

    def test_the_text_must_be_what_is_there(self):
        with pytest.raises(TextChanged):
            join_lines_at(generated(self.TEXT), 7, 13, "kota\nB")

    @pytest.mark.parametrize("start, end", [(-1, 6), (7, 99), (13, 7)])
    def test_offsets_outside_the_text(self, start, end):
        with pytest.raises(TextChanged):
            join_lines_at(generated(self.TEXT), start, end, "kota\nA")

    def test_an_empty_line_between_two_paragraphs_is_removed(self):
        result = self.join("Ale\n\nNowy")

        assert tei_xml_to_text(result) == "Ala ma kota\nA kot ma Ale\nNowy akapit"
        assert "<p>Ala ma kota<lb/>A kot ma Ale<lb/>Nowy akapit</p>" in result

    def test_removing_an_empty_line_twice_joins_the_lines(self):
        source = "315\n\nbłędy zbyt"
        once = join_lines_at(generated(source), 0, 10, "315\n\nbłędy")
        assert tei_xml_to_text(once) == "315\nbłędy zbyt"

        twice = join_lines_at(once, 0, 9, "315\nbłędy")

        assert tei_xml_to_text(twice) == "315 błędy zbyt"

    def test_the_markup_of_the_second_paragraph_comes_along(self):
        xml = tag_text_range(generated("315\n\nAla Jan ma"), 9, 12, "persName", "Jan")

        result = join_lines_at(xml, 0, 8, "315\n\nAla")

        assert tei_xml_to_text(result) == "315\nAla Jan ma"
        assert "<p>315<lb/>Ala <persName>Jan</persName> ma</p>" in result

    def test_a_hyphen_before_an_empty_line_makes_the_break_one_inside_a_word(self):
        result = join_lines_at(generated("kro-\n\nkodyl"), 0, 11, "kro-\n\nkodyl")

        assert '<lb break="no"/>' in result

    def test_empty_paragraphs_between_are_dropped(self):
        xml = generated("Ala\n\nma").replace("</p><p>", "</p><p/><p>")

        result = join_lines_at(xml, 0, 7, "Ala\n\nma")

        assert tei_xml_to_text(result) == "Ala\nma"
        assert "<p/>" not in result

    def test_a_head_is_not_merged_with_a_paragraph(self):
        xml = generated("Ala\n\nma").replace("<p>Ala</p>", "<head>Ala</head>")

        with pytest.raises(CannotJoinLines):
            join_lines_at(xml, 0, 7, "Ala\n\nma")

    def test_paragraphs_in_different_divs_are_not_merged(self):
        xml = generated("Ala\n\nma").replace(
            "<p>Ala</p><p>ma</p>", "<div><p>Ala</p></div><div><p>ma</p></div>"
        )

        with pytest.raises(CannotJoinLines):
            join_lines_at(xml, 0, 7, "Ala\n\nma")

    def test_the_end_of_an_l_is_not_a_plain_line_break(self):
        xml = generated("x").replace("<p>x</p>", "<lg><l>Ala</l><l>ma</l></lg>")
        assert tei_xml_to_text(xml) == "Ala\nma"

        with pytest.raises(CannotJoinLines):
            join_lines_at(xml, 0, 6, "Ala\nma")

    @pytest.mark.parametrize(
        "text",
        [
            "kota",  # one token
            "kota A",  # a space is not a line break
            "kota\n\n\nA",  # more than one empty line
            "kota \nA",  # a space next to the break
            "kota\nA kot",  # three tokens
            "\nkota\nA",
            "kota\tA",
        ],
    )
    def test_the_text_must_be_two_tokens_with_one_line_break(self, text):
        with pytest.raises(CannotJoinLines):
            join_lines_at(generated(self.TEXT), 7, 7 + len(text), text)

    def test_rejects_xml_that_is_not_tei(self):
        with pytest.raises(InvalidTei):
            join_lines_at("<a/>", 0, 3, "a\nb")


class TestReferencesBibliographyAndQuotations:
    TEXT = "Ala ma kota. Jan Kowalski tu."

    def tag(self, label, xml=None):
        return tag_text_range(
            xml or generated(self.TEXT), 13, 25, label, "Jan Kowalski"
        )

    @pytest.mark.parametrize("label", ["ref", "bibl"])
    def test_surrounds_the_text_with_the_element(self, label):
        assert f"<{label}>Jan Kowalski</{label}>" in self.tag(label)

    def test_a_citation_holds_the_quotation_in_a_quote(self):
        assert "<cit><quote>Jan Kowalski</quote></cit>" in self.tag("cit")

    @pytest.mark.parametrize("label", ["ref", "bibl", "cit"])
    def test_the_text_is_the_same(self, label):
        assert tei_xml_to_text(self.tag(label)) == self.TEXT

    @pytest.mark.parametrize("label", ["ref", "bibl", "cit"])
    def test_marking_it_again_changes_nothing(self, label):
        once = self.tag(label)

        assert self.tag(label, once) == once

    @pytest.mark.parametrize("label", ["ref", "bibl", "cit"])
    def test_is_read_back_from_the_xml_with_its_label(self, label):
        assert tei_xml_entities(self.tag(label)) == [
            {"label": label, "text": "Jan Kowalski", "start": 13, "end": 25}
        ]

    def test_a_citation_may_have_a_reference_inside(self):
        xml = self.tag("cit")

        result = tag_text_range(xml, 13, 16, "ref", "Jan")

        assert "<cit><quote><ref>Jan</ref> Kowalski</quote></cit>" in result

    def test_a_citation_across_a_line_break(self):
        xml = generated("Ala ma\nkota tu")

        result = tag_text_range(xml, 4, 11, "cit", "ma\nkota")

        assert tei_xml_to_text(result) == "Ala ma\nkota tu"
        assert "<cit><quote>ma<lb/>kota</quote></cit>" in result.replace("\n", "")

    def test_another_label_is_still_refused(self):
        with pytest.raises(CannotTag, match="not an entity label"):
            self.tag("quote")


class TestEntitiesOfTheXml:
    def test_nothing_is_marked(self):
        assert tei_xml_entities(generated("Ala ma kota")) == []

    def test_each_entity_with_its_label_text_and_place(self):
        text = "Jan Kowalski mieszka w Paryżu od 1840 roku."
        xml = generated(text)
        xml = tag_text_range(xml, 0, 12, "persName", "Jan Kowalski")
        xml = tag_text_range(xml, 23, 29, "placeName", "Paryżu")

        found = tei_xml_entities(xml)

        assert found == [
            {"label": "persName", "text": "Jan Kowalski", "start": 0, "end": 12},
            {"label": "placeName", "text": "Paryżu", "start": 23, "end": 29},
        ]
        assert all(text[e["start"] : e["end"]] == e["text"] for e in found)

    def test_all_the_labels(self):
        text = "a b c d e f"
        xml = generated(text)
        labels = ["persName", "placeName", "orgName", "geogName", "date", "time"]
        for i, label in enumerate(labels):
            xml = tag_text_range(xml, 2 * i, 2 * i + 1, label, text[2 * i])

        assert [e["label"] for e in tei_xml_entities(xml)] == labels

    def test_an_element_that_somebody_else_put_there_counts_too(self):
        xml = generated("W roku 1840 był tu Jan.").replace(
            "1840", '<date when="1840">1840</date>'
        )

        (entity,) = tei_xml_entities(xml)

        assert (entity["label"], entity["text"]) == ("date", "1840")

    def test_an_entity_over_a_line_break_has_the_break_in_its_text(self):
        xml = generated("Jan Ko\nwalski tu")
        text = tei_xml_to_text(xml)
        xml = tag_text_range(xml, 0, 13, "persName", text[:13])

        (entity,) = tei_xml_entities(xml)

        assert text[entity["start"] : entity["end"]] == entity["text"] == text[:13]

    def test_counts_code_points_after_emoji(self):
        text = "😀 Jan Kowalski"
        xml = tag_text_range(generated(text), 2, 14, "persName", "Jan Kowalski")

        (entity,) = tei_xml_entities(xml)

        assert (entity["start"], entity["end"]) == (2, 14)
        assert text[entity["start"] : entity["end"]] == "Jan Kowalski"

    def test_sections_are_not_entities(self):
        xml = mark(generated("84 KATEDRA\n\nAla"), "84 KATEDRA", "header")

        assert tei_xml_entities(xml) == []

    def test_rejects_xml_that_is_not_tei(self):
        with pytest.raises(InvalidTei):
            tei_xml_entities("<a/>")


class TestSectionsOfTheXml:
    def marked(self):
        xml = generated("84 KATEDRA\n\nAla 1 jeden 2 dwa b\n\nstopka 85")
        xml = mark(xml, "84 KATEDRA", "header")
        xml = mark(xml, "1 jeden", "note", n="1")
        xml = mark(xml, "2 dwa", "note", n="2")
        xml = mark(xml, "1 jeden 2 dwa", "note_group")
        return mark(xml, "stopka 85", "footer")

    def test_nothing_is_marked(self):
        assert tei_xml_sections(generated("Ala ma kota")) == []

    def test_each_section_with_its_place_and_tags(self):
        xml = self.marked()
        text = tei_xml_to_text(xml)

        found = [
            (text[s["start"] : s["end"]], s["open"], s["close"])
            for s in tei_xml_sections(xml)
        ]

        assert found == [
            ("84 KATEDRA", '<fw type="header">', "</fw>"),
            ("1 jeden 2 dwa", "<noteGrp>", "</noteGrp>"),
            ("1 jeden", '<note n="1">', "</note>"),
            ("2 dwa", '<note n="2">', "</note>"),
            ("stopka 85", '<fw type="footer">', "</fw>"),
        ]

    def test_a_table_of_contents_spans_its_paragraphs(self):
        xml = mark(generated("a\n\nb\n\nc\n\nd"), "b\n\nc", "toc")
        text = tei_xml_to_text(xml)

        (toc,) = tei_xml_sections(xml)

        assert text[toc["start"] : toc["end"]] == "b\n\nc"
        assert toc["open"] == '<div type="toc">'

    def test_the_plain_divs_after_a_table_of_contents_are_not_sections(self):
        xml = mark(generated("a\n\nb\n\nc"), "a", "toc")

        assert len(tei_xml_sections(xml)) == 1

    def test_entities_are_not_sections(self):
        xml = tag_text_range(
            generated("Jan Kowalski"), 0, 12, "persName", "Jan Kowalski"
        )

        assert tei_xml_sections(xml) == []

    def test_offsets_count_code_points(self):
        xml = mark(generated("😀 a 84 b"), "84", "page_number")
        text = tei_xml_to_text(xml)

        (section,) = tei_xml_sections(xml)

        assert text[section["start"] : section["end"]] == "84"

    def test_attribute_values_are_escaped(self):
        xml = mark(generated("a 1 b"), "1", "note", n='x"y<')

        (section,) = tei_xml_sections(xml)

        assert section["open"] == "<note n='x\"y&lt;'>"

    @pytest.mark.parametrize("xml", ["", "not xml", "<a/>"])
    def test_xml_that_is_not_tei(self, xml):
        with pytest.raises(InvalidTei):
            tei_xml_sections(xml)
