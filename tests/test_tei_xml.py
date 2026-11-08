import delb
import pytest

from app.tei_xml import TEI_NAMESPACE, InvalidTei, tei_xml_to_text, text_to_tei_xml

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
