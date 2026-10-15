"""Online 1-second PetSense IMU action inference.

The collector calls :meth:`ActionClassifier.ingest` for every sensor frame.  A
prediction is emitted only after a device-time second is complete, so the
window boundary is independent from network latency and MQTT batching.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import joblib
import numpy as np


AXES = (
    "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
    "roll_deg", "pitch_deg", "yaw_deg",
)
ACTION_KEYS = {
    "01_standing": "stand",
    "02_sitting": "sit",
    "03_resting": "lie_down",
    "05_walking": "walk",
    "06_running": "run",
    "10_jumping": "jump",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sample_from_frame(frame: dict) -> dict | None:
    imu = frame.get("imu")
    if not isinstance(imu, dict) or imu.get("ok") is False:
        return None
    accel, gyro, euler = imu.get("accel"), imu.get("gyro"), imu.get("euler")
    if not all(isinstance(vector, (list, tuple)) and len(vector) >= 3 for vector in (accel, gyro, euler)):
        return None
    try:
        values = [float(value) for vector in (accel, gyro, euler) for value in vector[:3]]
    except (TypeError, ValueError):
        return None
    if not np.isfinite(values).all():
        return None
    return dict(zip(AXES, values))


def features_from_samples(samples: list[dict]) -> dict[str, float]:
    arrays = {axis: np.asarray([row[axis] for row in samples], dtype=float) for axis in AXES}
    output: dict[str, float] = {"sample_count": float(len(samples))}
    for axis, values in arrays.items():
        output[f"{axis}_mean"] = float(np.mean(values))
        output[f"{axis}_std"] = float(np.std(values))
        output[f"{axis}_min"] = float(np.min(values))
        output[f"{axis}_max"] = float(np.max(values))
        output[f"{axis}_median"] = float(np.median(values))
        output[f"{axis}_range"] = output[f"{axis}_max"] - output[f"{axis}_min"]
        output[f"{axis}_rms"] = float(np.sqrt(np.mean(values * values)))
    acc_mag = np.sqrt(arrays["ax_g"] ** 2 + arrays["ay_g"] ** 2 + arrays["az_g"] ** 2)
    gyro_mag = np.sqrt(arrays["gx_dps"] ** 2 + arrays["gy_dps"] ** 2 + arrays["gz_dps"] ** 2)
    for name, values in (("acc_mag", acc_mag), ("gyro_mag", gyro_mag)):
        output[f"{name}_mean"] = float(np.mean(values))
        output[f"{name}_std"] = float(np.std(values))
        output[f"{name}_max"] = float(np.max(values))
        output[f"{name}_rms"] = float(np.sqrt(np.mean(values * values)))
    return output


class ActionClassifier:
    def __init__(self, model_path: str, model_version: str = "2026-10-14-device-clock-v1", minimum_samples: int = 2):
        package = joblib.load(model_path)
        self.model = package["model"]
        self.features = package["features"]
        self.labels = package["labels"]
        self.model_version = model_version
        self.minimum_samples = minimum_samples
        self._windows: dict[str, dict] = {}
        self._latest: dict[str, dict] = {}
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": True,
                "source": "random_forest_1s",
                "modelVersion": self.model_version,
                "windowSeconds": 1,
                "minimumSamples": self.minimum_samples,
                "classes": self.labels,
                "activeDevices": len(self._windows),
            }

    def latest(self, device_id: str | None = None) -> dict | None:
        with self._lock:
            if device_id:
                value = self._latest.get(device_id)
            else:
                value = max(self._latest.values(), key=lambda item: item["predictedAt"], default=None)
            return dict(value) if value else None

    def _predict(self, device_id: str, bucket: int, samples: list[dict]) -> dict:
        feature_map = features_from_samples(samples)
        vector = np.asarray([[feature_map.get(name, np.nan) for name in self.features]], dtype=float)
        event_id = str(self.model.predict(vector)[0])
        probabilities = self.model.predict_proba(vector)[0]
        classes = [str(value) for value in self.model.classes_]
        scores = [
            {
                "eventId": candidate,
                "label": self.labels[candidate],
                "actionKey": ACTION_KEYS.get(candidate, "stand"),
                "probability": round(float(probability), 6),
            }
            for candidate, probability in zip(classes, probabilities)
        ]
        scores.sort(key=lambda row: row["probability"], reverse=True)
        return {
            "source": "random_forest_1s",
            "modelVersion": self.model_version,
            "deviceId": device_id,
            "eventId": event_id,
            "label": self.labels[event_id],
            "actionKey": ACTION_KEYS.get(event_id, "stand"),
            "confidence": scores[0]["probability"],
            "scores": scores,
            "sampleCount": len(samples),
            "windowStartTsMs": bucket * 1000,
            "windowEndTsMs": (bucket + 1) * 1000,
            "predictedAt": _utc_now(),
        }

    def ingest(self, frame: dict) -> tuple[dict | None, bool]:
        sample = _sample_from_frame(frame)
        device_id = frame.get("deviceId")
        timestamp = frame.get("ts_ms")
        if sample is None or not isinstance(device_id, str) or not isinstance(timestamp, (int, float)):
            return self.latest(device_id if isinstance(device_id, str) else None), False
        bucket = int(timestamp // 1000)
        with self._lock:
            state = self._windows.get(device_id)
            if state is None or bucket < state["bucket"]:
                self._windows[device_id] = {"bucket": bucket, "samples": [sample]}
                return self._latest.get(device_id), False
            if bucket == state["bucket"]:
                state["samples"].append(sample)
                return self._latest.get(device_id), False

            prediction = None
            if len(state["samples"]) >= self.minimum_samples:
                prediction = self._predict(device_id, state["bucket"], state["samples"])
                self._latest[device_id] = prediction
            self._windows[device_id] = {"bucket": bucket, "samples": [sample]}
            return prediction or self._latest.get(device_id), prediction is not None
