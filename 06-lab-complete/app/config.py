"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường (tiêu chí 9).

Mỗi biến đều có giá trị mặc định an toàn để chạy local, riêng AGENT_API_KEY
mặc định là chuỗi rỗng: không đặt key thì mọi request /ask đều bị 401
(an toàn hơn là tự đặt một key mặc định trong code).
"""
import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    """Đọc biến môi trường kiểu số nguyên, sai định dạng thì dùng mặc định."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Đọc biến môi trường kiểu số thực, sai định dạng thì dùng mặc định."""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # Server
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Bảo mật — KHÔNG có giá trị mặc định cho secret
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # Redis — nơi lưu state (rate limit, chi phí) để agent stateless
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Giới hạn
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))


settings = Settings()
