"""Event consumers: what runs when a domain event happens."""

from app.async_tasking.domain_events import NewEditionCreatedEvent


class NewEditionCreatedEventConsumer:
    """Listens to `NewEditionCreatedEvent`."""

    def handle(self, event: NewEditionCreatedEvent) -> None:
        """React to a new edition: for now, print its UUID."""
        # flush: shows in the container logs at once, not when the buffer fills
        print(f"***** {event.edition_id} *****", flush=True)
