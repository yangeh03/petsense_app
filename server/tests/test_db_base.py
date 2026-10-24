import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Column, Integer, MetaData, Table, text

from petsense.db import configure, engine, iso_z, session_scope, utc_now
from petsense.db.base import UTCDatetime


class DbBaseTests(unittest.TestCase):
    def make_engine(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return configure('sqlite:///' + holder.name + '/t.db')

    def test_iso_z_matches_wire_format(self):
        moment = datetime(2026, 10, 24, 4, 5, 6, 123456, tzinfo=timezone.utc)
        self.assertEqual(iso_z(moment), '2026-10-24T04:05:06.123Z')

    def test_utc_now_is_aware(self):
        self.assertIsNotNone(utc_now().tzinfo)

    def test_sqlite_pragmas_active(self):
        eng = self.make_engine()
        with eng.connect() as conn:
            mode = conn.execute(text('PRAGMA journal_mode')).scalar()
            sync = conn.execute(text('PRAGMA synchronous')).scalar()
            fk = conn.execute(text('PRAGMA foreign_keys')).scalar()
        self.assertEqual(mode, 'wal')
        self.assertEqual(sync, 2)  # FULL
        self.assertEqual(fk, 1)

    def test_utc_datetime_roundtrip_keeps_awareness(self):
        metadata = MetaData()
        probe = Table('probe', metadata, Column('id', Integer, primary_key=True),
                      Column('at', UTCDatetime, nullable=False))
        metadata.create_all(self.make_engine())
        original = datetime(2026, 10, 24, 8, 0, 0, 500000, tzinfo=timezone.utc)
        with session_scope() as s:
            s.execute(probe.insert().values(id=1, at=original))
        with session_scope() as s:
            loaded = s.execute(probe.select()).mappings().first()['at']
        self.assertIsNotNone(loaded.tzinfo)
        self.assertEqual(loaded, original)


if __name__ == '__main__':
    unittest.main()
