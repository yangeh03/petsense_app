"""Durable social notification outbox; delivery may run more than once."""

import json
import os
import threading
import time
from datetime import timedelta

from sqlalchemy import delete, or_, select

from petsense import config
from petsense.db import dialect_insert, session_scope, utc_now
from petsense.db.models import (DiscoveryLocation, DiscoveryQuery, Post, SocialNotification,
                                SocialOutbox, Story, StoryView, User)


def queue_social_event(session, key, kind, actor_id, recipient_id, post_id=None, comment_id=None):
    if actor_id == recipient_id:
        return
    payload = {'actorId': actor_id, 'recipientId': recipient_id,
               'postId': post_id, 'commentId': comment_id}
    stmt = dialect_insert(session, SocialOutbox).values(
        event_key=key, kind=kind, payload_json=json.dumps(payload),
        status='pending', attempts=0, next_attempt_at=utc_now())
    session.execute(stmt.on_conflict_do_nothing(index_elements=['event_key']))


def drain_social_outbox(limit=50):
    """Claim expired work and materialize notifications with a unique dedup key."""
    delivered = 0
    for _ in range(limit):
        now = utc_now()
        with session_scope() as s:
            job = s.execute(select(SocialOutbox).where(or_(
                (SocialOutbox.status == 'pending') & (SocialOutbox.next_attempt_at <= now),
                (SocialOutbox.status == 'working') & (SocialOutbox.lease_until <= now)))
                .order_by(SocialOutbox.id).limit(1)).scalar_one_or_none()
            if not job:
                break
            job.status = 'working'
            job.lease_until = now + timedelta(seconds=30)
            job_id = job.id
        try:
            with session_scope() as s:
                job = s.get(SocialOutbox, job_id)
                if not job or job.status == 'done':
                    continue
                payload = json.loads(job.payload_json)
                actor = s.get(User, payload['actorId'])
                recipient = s.get(User, payload['recipientId'])
                post_id = payload.get('postId')
                post = s.get(Post, post_id) if post_id else None
                if actor and recipient and (not post_id or (post and post.deleted_at is None)):
                    stmt = dialect_insert(s, SocialNotification).values(
                        recipient_id=recipient.id, actor_id=actor.id, kind=job.kind,
                        post_id=post_id, comment_id=payload.get('commentId'),
                        created_at=utc_now(), dedup_key=job.event_key)
                    s.execute(stmt.on_conflict_do_nothing(index_elements=['dedup_key']))
                    delivered += 1
                job.status = 'done'
                job.lease_until = None
        except Exception:
            with session_scope() as s:
                job = s.get(SocialOutbox, job_id)
                if job:
                    job.attempts += 1
                    job.status = 'dead' if job.attempts >= 10 else 'pending'
                    job.next_attempt_at = utc_now() + timedelta(seconds=min(3600, 2 ** job.attempts))
                    job.lease_until = None
    return delivered


def prune_expired_social_data(limit=500):
    """Remove expired story assets and stale coarse discovery state in bounded batches."""
    now = utc_now()
    with session_scope() as s:
        expired = s.execute(select(Story.id, Story.image_path).where(
            Story.expires_at <= now).order_by(Story.id).limit(limit)).all()
        story_ids = [row.id for row in expired]
        for row in expired:
            try:
                os.remove(os.path.join(config.SOCIAL_MEDIA_DIR, row.image_path))
            except FileNotFoundError:
                pass
        if story_ids:
            s.execute(delete(StoryView).where(StoryView.story_id.in_(story_ids)))
            s.execute(delete(Story).where(Story.id.in_(story_ids)))
        s.execute(delete(DiscoveryQuery).where(DiscoveryQuery.expires_at <= now))
        s.execute(delete(DiscoveryLocation).where(DiscoveryLocation.expires_at <= now))
    return len(story_ids)


def start_social_worker():
    """Run in the server process; another worker can replay leased work after restart."""
    def loop():
        last_prune = 0.0
        while True:
            try:
                drain_social_outbox()
                if time.monotonic() - last_prune >= 3600:
                    prune_expired_social_data()
                    last_prune = time.monotonic()
            except Exception:
                pass
            threading.Event().wait(2)
    threading.Thread(target=loop, name='petsense-social-outbox', daemon=True).start()
