from dataclasses import dataclass


@dataclass(frozen=True)
class MeasurementFrame:
    distances_m: dict[int, float]
    source: str = 'mqtt'
    tag_id: int = 0
    sequence: int = 0
    device_timer: int = 0
