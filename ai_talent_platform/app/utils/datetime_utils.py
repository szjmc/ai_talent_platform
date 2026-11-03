"""
模块：app/utils/datetime_utils.py
职责：时间处理工具。

实现思路：
- 全平台统一使用「无时区本地时间」（数据库 DATETIME 存本地时间），
  仅 JWT 用 UTC 时间戳，避免混用导致 8 小时偏差；
- 提供常用格式化、区间解析（today / last_7_days / custom）、相对时间描述。

待实现清单：
    - [ ] now() / today_start() / today_end()
    - [ ] format_dt(dt, fmt="%Y-%m-%d %H:%M:%S")
    - [ ] parse_date_range(start: str, end: str) -> tuple[datetime, datetime]
    - [ ] humanize(dt) -> "3 分钟前"
    - [ ] month_range(year, month) -> (start, end)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def now() -> datetime:
    """当前时间（秒级，去掉微秒，便于比较与展示）。"""
    return datetime.now().replace(microsecond=0)


def today_start() -> datetime:
    return datetime.combine(date.today(), time.min)


def today_end() -> datetime:
    return datetime.combine(date.today(), time.max)


def format_dt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
    return value.strftime(fmt) if value else None


def days_ago(days: int) -> datetime:
    return now() - timedelta(days=days)


def month_range(year: int, month: int) -> tuple[datetime, datetime]:
    """返回某月的起止时间（用于趋势统计）。"""
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(seconds=1)
    return start, end


def humanize(value: datetime | None) -> str:
    """相对时间描述。"""
    if value is None:
        return ""
    delta = now() - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} 天前"
    return value.strftime("%Y-%m-%d")
