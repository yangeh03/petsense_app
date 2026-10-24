import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from petsense import db
from petsense.db.models import DailyStat, Pet, Post, PostLike, Reading, User
from helpers import fresh_db


def make_user(session, username='mike'):
    user = User(username=username, display_name=username, password_hash='x', salt='s',
                avatar_emoji='🙂', bio='', created_at=db.utc_now())
    session.add(user)
    session.flush()
    return user


class ModelBehaviorTests(unittest.TestCase):
    def setUp(self):
        fresh_db(self)

    def test_duplicate_username_any_case_rejected(self):
        with db.session_scope() as s:
            make_user(s, 'mike')
        with self.assertRaises(IntegrityError):
            with db.session_scope() as s:
                make_user(s, 'MIKE')
        with db.session_scope() as s:
            self.assertEqual(len(s.execute(select(User)).scalars().all()), 1)

    def test_like_requires_existing_post_and_user(self):
        with db.session_scope() as s:
            user = make_user(s)
            post = Post(user_id=user.id, image_path='', caption='hi', location_text='',
                        created_at=db.utc_now())
            s.add(post)
            s.flush()
            s.add(PostLike(post_id=post.id, user_id=user.id, created_at=db.utc_now()))
        with self.assertRaises(IntegrityError):
            with db.session_scope() as s:
                s.add(PostLike(post_id=99999, user_id=1, created_at=db.utc_now()))

    def test_daily_stats_upsert_accumulates(self):
        with db.session_scope() as s:
            user = make_user(s)
            pet = Pet(user_id=user.id, name='Max', species='dog', breed='', avatar_emoji='🐾',
                      device_id='', created_at=db.utc_now())
            s.add(pet)
            s.flush()
            pet_id = pet.id

        def upsert(sleep, active, hr_sum, hr_count):
            with db.session_scope() as s:
                stmt = db.dialect_insert(s, DailyStat).values(
                    pet_id=pet_id, date=date(2026, 10, 24), sleep_seconds=sleep,
                    active_seconds=active, hr_sum=hr_sum, hr_count=hr_count, samples=1)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['pet_id', 'date'],
                    set_={'sleep_seconds': DailyStat.__table__.c.sleep_seconds + stmt.excluded.sleep_seconds,
                          'active_seconds': DailyStat.__table__.c.active_seconds + stmt.excluded.active_seconds,
                          'hr_sum': DailyStat.__table__.c.hr_sum + stmt.excluded.hr_sum,
                          'hr_count': DailyStat.__table__.c.hr_count + stmt.excluded.hr_count,
                          'samples': DailyStat.__table__.c.samples + 1})
                s.execute(stmt)

        upsert(60, 30, 90.0, 1)
        upsert(60, 30, 110.0, 1)
        with db.session_scope() as s:
            row = s.get(DailyStat, (pet_id, date(2026, 10, 24)))
            self.assertEqual((row.sleep_seconds, row.samples, row.hr_count), (120, 2, 2))

    def test_reading_roundtrip_aware(self):
        moment = datetime(2026, 10, 24, 1, 2, 3, 400000, tzinfo=timezone.utc)
        with db.session_scope() as s:
            s.add(Reading(topic='petsense/sensor/x', payload='{}', received_at=moment))
        with db.session_scope() as s:
            loaded = s.execute(select(Reading.received_at)).scalar_one()
        self.assertEqual(loaded, moment)


if __name__ == '__main__':
    unittest.main()
