import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collection_api import CollectionStore


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.sent = []
        self.store = CollectionStore(self.temporary.name, "test-token", self.publish)
        app = Flask(__name__)
        app.register_blueprint(self.store.blueprint())
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-token"}
        self.frame = dict(boot_id="boot-1", seq=1, mono_us=100000, utc_us=1000000,
                          session_id="session-1", imu={"ok": True, "accel": [0, 0, 1]})

    def publish(self, device, message):
        if message.get("type") == "collection_ack":
            # The publishing callback uses a new connection: ACK must follow commit.
            with self.store.db() as db:
                self.assertGreater(db.execute("SELECT count(*) FROM frames").fetchone()[0], 0)
        self.sent.append((device, message))
        return True

    def batch(self, frames=None, **extra):
        return dict(device="device-1", batch="batch-1",
                    frames=frames or [copy.deepcopy(self.frame)], **extra)

    def test_authentication_required(self):
        self.assertEqual(self.client.get("/api/collection/v1/time").status_code, 401)
        self.assertEqual(self.client.get("/api/collection/v1/time", headers=self.headers).status_code, 200)

    def test_commit_before_ack_and_repeated_batches_are_idempotent(self):
        for _ in range(2):
            self.store.ingest("petsense/sensor/collection_v2", self.batch(crcs=[123]))
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM frames").fetchone()[0], 1)
        self.assertEqual(len(self.sent), 2)

    def test_conflicting_sequence_has_no_ack(self):
        self.store.ingest("petsense/sensor/collection_v2", self.batch())
        other = copy.deepcopy(self.frame)
        other["mono_us"] += 1
        with self.assertRaises(ValueError):
            self.store.ingest("petsense/sensor/collection_v2", self.batch([other]))
        self.assertEqual(len(self.sent), 1)

    def test_delta_batch_reconstructs_nested_fields(self):
        self.store.ingest("petsense/sensor/collection_v2", self.batch(
            [self.frame, dict(seq=2, mono_us=200000, imu={"ok": False})], delta=True))
        with self.store.db() as db:
            import json
            frame = json.loads(db.execute("SELECT document FROM frames WHERE seq=2").fetchone()[0])
        self.assertEqual(frame["imu"], {"ok": False, "accel": [0, 0, 1]})

    def test_clock_reply_and_start_requires_fresh_synchronized_status(self):
        self.store.ingest("petsense/sensor/clock_v2", dict(device="device-1", boot="boot-1", nonce=1, t0_us=2))
        self.assertEqual(self.sent[-1][1]["type"], "clock_reply")
        response = self.client.post("/api/collection/v1/sessions", headers=self.headers,
                                    json=dict(id="session-1", device="device-1"))
        self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/collection/v1/sessions/session-1/control",
                                    headers=self.headers, json={"action": "start"})
        self.assertEqual(response.status_code, 409)

    def test_video_upload_rejects_wrong_checksum_and_offset(self):
        self.client.post("/api/collection/v1/sessions", headers=self.headers,
                         json=dict(id="session-1", device="device-1"))
        base = "/api/collection/v1/sessions/session-1/videos"
        self.assertEqual(self.client.post(base, headers=self.headers,
            json=dict(id="video-1", event_id="01_standing", start_utc_ms=1000)).status_code, 201)
        url = base + "/video-1/upload?offset=0"
        data = b"not a real video"
        self.assertEqual(self.client.put(url, headers=self.headers, data=data).status_code, 422)
        headers = {**self.headers, "X-Chunk-SHA256": hashlib.sha256(data).hexdigest()}
        self.assertEqual(self.client.put(url, headers=headers, data=data).status_code, 200)
        self.assertEqual(self.client.put(url, headers=headers, data=data).status_code, 409)
        with patch.object(self.store, "validate_media") as validate:
            response = self.client.post(base + "/video-1/finish", headers=self.headers,
                                         json={"size": len(data)})
            self.assertEqual(response.status_code, 422)
            validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
