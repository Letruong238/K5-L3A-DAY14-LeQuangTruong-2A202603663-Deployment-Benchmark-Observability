"""
Rate limiting (tiêu chí 7) — sliding window bằng Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, restart agent vẫn còn
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket_key(key: str) -> str:
    # Không lưu API key gốc vào Redis, chỉ lưu bản băm (hash)
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:32]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket_key(key)
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex[:8]}"   # mỗi request một phần tử riêng

    # MULTI/EXEC: 4 lệnh chạy liền một khối, các worker không chen ngang được
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)   # bỏ request cũ hơn 60s
    pipe.zadd(bucket, {member: now})                          # ghi request hiện tại
    pipe.zcard(bucket)                                        # đếm request trong cửa sổ
    pipe.expire(bucket, WINDOW_SECONDS + 1)                   # tự xoá key khi không dùng
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)   # request bị từ chối thì không tính vào cửa sổ
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = WINDOW_SECONDS
        if oldest:
            retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit_per_minute": limit,
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
