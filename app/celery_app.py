"""The Celery app: the worker (`celery -A app.celery_app worker`, the
`celery-worker` container) and the backend send and run tasks through it.

The app is configured with the broker. Its one task runs the consumer of
`NewEditionCreatedEvent`.
"""

import os

from celery import Celery

from app.async_tasking.domain_events import NewEditionCreatedEvent
from app.async_tasking.event_consumers import NewEditionCreatedEventConsumer

# In docker compose the broker is the `rabbitmq` container (see
# docker-compose.yml); the default is RabbitMQ on the host, as it comes out of
# the box.
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")

celery_app = Celery("tiron", broker=BROKER_URL)


@celery_app.task(name="handle_new_edition_created")
def handle_new_edition_created(event: dict) -> None:
    """Run `NewEditionCreatedEventConsumer` on the event, in the worker.

    The event travels through the broker as JSON, so it is sent as a dict
    (`event.model_dump(mode="json")`) and rebuilt here.
    """
    NewEditionCreatedEventConsumer().handle(
        NewEditionCreatedEvent.model_validate(event)
    )
