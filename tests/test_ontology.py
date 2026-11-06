from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.ontology import Collection, Correction, Edition, Page, Work

WORK_ID = uuid4()


def test_work_generates_uuid():
    work = Work(author="Bolesław Prus")

    assert isinstance(work.id, UUID)
    assert work.author == "Bolesław Prus"


def test_work_ids_are_unique():
    assert Work(author="a").id != Work(author="a").id


def test_work_author_and_title_are_optional():
    work = Work()

    assert work.author is None
    assert work.title is None


def test_edition_generates_uuid():
    edition = Edition(work_id=WORK_ID, place="Warszawa", year=1890)

    assert isinstance(edition.id, UUID)
    assert edition.place == "Warszawa"
    assert edition.year == 1890


@pytest.mark.parametrize("year", [1500, 2030])
def test_edition_year_bounds_inclusive(year):
    assert Edition(work_id=WORK_ID, place="Kraków", year=year).year == year


@pytest.mark.parametrize("year", [1499, 2031])
def test_edition_year_out_of_range(year):
    with pytest.raises(ValidationError):
        Edition(work_id=WORK_ID, place="Kraków", year=year)


def test_edition_references_work():
    work = Work(author="Bolesław Prus")

    assert Edition(work_id=work.id, place="Warszawa", year=1890).work_id == work.id


def test_edition_work_id_required():
    with pytest.raises(ValidationError):
        Edition(place="Warszawa", year=1890)


def test_page_fields():
    edition = Edition(work_id=WORK_ID, place="Warszawa", year=1890)

    page = Page(edition_id=edition.id, number=1, raw_content="Ala ma kota")

    assert isinstance(page.id, UUID)
    assert page.edition_id == edition.id
    assert page.number == 1
    assert page.raw_content == "Ala ma kota"
    assert page.corrected_content is None


def test_page_with_corrected_content():
    page = Page(
        edition_id=uuid4(), number=2, raw_content="Aia ma kota", corrected_content="Ala"
    )

    assert page.corrected_content == "Ala"


@pytest.mark.parametrize("missing", ["edition_id", "number", "raw_content"])
def test_page_required_fields(missing):
    data = {"edition_id": uuid4(), "number": 1, "raw_content": "x"}
    del data[missing]

    with pytest.raises(ValidationError):
        Page(**data)


def test_page_number_can_be_zero():
    assert Page(edition_id=uuid4(), number=0, raw_content="x").number == 0


def test_page_number_cannot_be_negative():
    with pytest.raises(ValidationError):
        Page(edition_id=uuid4(), number=-1, raw_content="x")


def test_correction_fields():
    page_id = uuid4()

    correction = Correction(page_id=page_id, before="Aia", after="Ala")

    assert isinstance(correction.id, UUID)
    assert correction.page_id == page_id
    assert correction.before == "Aia"
    assert correction.after == "Ala"


@pytest.mark.parametrize("field", ["before", "after"])
@pytest.mark.parametrize("value", ["", None])
def test_correction_rejects_empty_or_null(field, value):
    data = {"page_id": uuid4(), "before": "Aia", "after": "Ala", field: value}

    with pytest.raises(ValidationError):
        Correction(**data)


@pytest.mark.parametrize("missing", ["page_id", "before", "after"])
def test_correction_required_fields(missing):
    data = {"page_id": uuid4(), "before": "Aia", "after": "Ala"}
    del data[missing]

    with pytest.raises(ValidationError):
        Correction(**data)


def test_collection_is_list_of_uuids():
    ids = [uuid4(), uuid4()]

    assert Collection(ids).root == ids
    assert Collection([]).root == []


def test_collection_parses_uuid_strings():
    uid = uuid4()

    assert Collection.model_validate([str(uid)]).root == [uid]


def test_collection_rejects_duplicates():
    uid = uuid4()

    with pytest.raises(ValidationError):
        Collection([uid, uid])


def test_collection_rejects_non_uuid():
    with pytest.raises(ValidationError):
        Collection(["not-a-uuid"])


def test_edition_place_and_year_are_optional():
    edition = Edition(work_id=WORK_ID)

    assert edition.place is None
    assert edition.year is None
