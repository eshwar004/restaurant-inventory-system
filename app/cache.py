import json
import os
import redis
from redis.exceptions import RedisError

redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)


def get_cache(key: str):
    try:
        value = redis_client.get(key)
        if value is None:
            return None
        return json.loads(value)
    except (RedisError, json.JSONDecodeError):
        return None


def set_cache(key: str, value, ttl: int = 60) -> None:
    try:
        redis_client.set(key, json.dumps(value), ex=ttl)
    except (RedisError, TypeError, ValueError):
        pass


def delete_cache(key: str) -> None:
    try:
        redis_client.delete(key)
    except RedisError:
        pass
