"""DeclarativeBase 与跨方言类型。"""
from datetime import timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class UTCDatetime(TypeDecorator):
    """统一时间类型：进出都是 aware UTC。

    SQLite 方言原生 DateTime 不携带时区（存取会丢 offset），这里在 bind 时
    归一成 naive-UTC、result 时补回 UTC；PG 方言直接用 timestamptz。
    """
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'sqlite':
            return dialect.type_descriptor(DateTime())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is not None and dialect.name == 'sqlite' and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and dialect.name == 'sqlite' and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
