
import json
import logging
import os

from kafka import KafkaProducer

logger = logging.getLogger(__name__)

_producer = None


def get_producer():
    """Create the Kafka producer only when it is first needed."""
    global _producer

    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS",
                "localhost:29092",
            ),
            key_serializer=lambda key: str(key).encode("utf-8"),
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
            request_timeout_ms=10000,
        )

    return _producer


def publish_order_event(event: dict) -> bool:
    """Publish an order event and wait for Kafka acknowledgement."""
    try:
        producer = get_producer()

        producer.send(
            "orders.events",
            key=event["order_id"],
            value=event,
        ).get(timeout=10)

        logger.info(
            "Published order event for order_id=%s",
            event["order_id"],
        )
        return True

    except Exception:
        logger.exception("Failed to publish order event to Kafka")
        return False
