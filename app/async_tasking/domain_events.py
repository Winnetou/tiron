"""Domain events: things that happened in the backend that others may react to."""

from uuid import UUID

from pydantic import BaseModel


class NewEditionCreatedEvent(BaseModel):
    """A new edition was saved in the database for the first time.

    `edition_id` is the Polona object UUID of the edition.
    """

    edition_id: UUID
