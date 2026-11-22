"""学习日累计回填核验工具（W05，agent-w05-spec.md §3.8，D3）。

从 TelemetryMinute.source_versions 的 "feature:version" 标注重建学习日累计，
与 agent_learning_days 现有行比对，输出差异报告。只读，不写任何表；
建议在隔离实例或云端只读副本上运行。

口径说明（近似性）：线上累计按帧间隔秒数记账（record_interval），本工具按分钟行
观测秒数重建（每分钟 ≤60s 归到标注版本）。同分钟混多个版本时各版本都计入该分钟
的观测秒数，与线上逐间隔记账存在少量偏差；差异报告按天给出秒数差与有效日判定差，
偏差在容差内不算异常。无任何版本标注的分钟行单列计数，不归集任何版本（P00 §1）。

用法：
  python3 agent_baseline_rebuild.py [--db /path/to/data.db] [--tolerance 600]
  缺省 --db 取 PETSENSE_ROOT（或包上一级运行目录）下的 data.db。
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

VALID_SECONDS = 21600
VALID_SLOTS = 2


def default_db():
    root = os.environ.get('PETSENSE_ROOT')
    if not root:
        root = str(Path(__file__).resolve().parents[2])
    return os.path.join(root, 'data.db')


def rebuild(connection):
    """返回 {(pet_id, device_id, feature, version, day): {'seconds', 'slots'}} 与无标注计数。"""
    rebuilt = defaultdict(lambda: {'seconds': 0.0, 'slots': set()})
    untagged = 0
    rows = connection.execute(
        'SELECT owner_id, device_id, minute, observed_seconds, sound_observed_seconds, source_versions '
        'FROM telemetry_minutes').fetchall()
    for owner_id, device_id, minute, observed, sound_observed, versions_json in rows:
        try:
            versions = json.loads(versions_json or '[]')
        except (TypeError, ValueError):
            versions = []
        if not isinstance(versions, list) or not versions:
            untagged += 1
            continue
        # minute 为 UTC ISO 文本（UTCDatetime 落库）；粗时段按上海时区（与 record_interval 口径一致）
        local = datetime.fromisoformat(minute).replace(tzinfo=timezone.utc).astimezone(
            timezone(timedelta(hours=8)))
        day = local.date().isoformat()
        slot = local.hour // 6
        for tag in versions:
            if not isinstance(tag, str) or ':' not in tag:
                continue
            feature, version = tag.split(':', 1)
            if feature not in ('actions', 'sounds'):
                continue
            entry = rebuilt[(owner_id, device_id, feature, version, day)]
            entry['seconds'] += min(60, observed if feature == 'actions' else sound_observed)
            entry['slots'].add(slot)
    return rebuilt, untagged


def main():
    parser = argparse.ArgumentParser(description='学习日累计回填核验（只读比对）')
    parser.add_argument('--db', default=default_db())
    parser.add_argument('--tolerance', type=float, default=600,
                        help='单日秒数容差（默认600秒，分钟级记账近似）')
    args = parser.parse_args()
    connection = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    rebuilt, untagged = rebuild(connection)
    stored = connection.execute(
        'SELECT pet_id, device_id, feature, version, day, seconds, slots FROM agent_learning_days'
    ).fetchall()

    def valid(seconds, slots_json):
        try:
            slot_count = len(json.loads(slots_json or '[]'))
        except (TypeError, ValueError):
            slot_count = 0
        return seconds >= VALID_SECONDS and slot_count >= VALID_SLOTS

    stored_by_key = {(r['pet_id'], r['device_id'], r['feature'], r['version'], r['day']): r
                     for r in stored}
    diffs, missing_in_stored, missing_in_rebuild = [], [], []
    for key, entry in sorted(rebuilt.items()):
        row = stored_by_key.get(key)
        if row is None:
            missing_in_stored.append(key)
            continue
        delta = abs(entry['seconds'] - row['seconds'])
        if delta > args.tolerance:
            diffs.append((key, entry['seconds'], row['seconds'], delta))
        if valid(entry['seconds'], json.dumps(sorted(entry['slots']))) != valid(row['seconds'], row['slots']):
            diffs.append((key, 'valid-day 判定不一致',
                          f"rebuilt={valid(entry['seconds'], '[]')}", f"stored={valid(row['seconds'], row['slots'])}"))
    for key in sorted(set(stored_by_key) - set(rebuilt)):
        missing_in_rebuild.append(key)

    print(f'比对完成：分钟行重建 {len(rebuilt)} 个 (宠物,版本,日) 键，线上学习日 {len(stored_by_key)} 行。')
    print(f'无版本标注分钟行（不归集，P00 §1）：{untagged} 行。')
    for title, items in (('线上缺失（分钟行有标注但学习日无行）', missing_in_stored),
                         ('重建缺失（学习日有行但分钟行无标注/已裁剪）', missing_in_rebuild),
                         (f'超出容差（>{args.tolerance:.0f}s）或判定不一致', diffs)):
        print(f'{title}：{len(items)}')
        for item in items[:20]:
            print('  ', item)
        if len(items) > 20:
            print(f'   …共 {len(items)} 条')
    connection.close()


if __name__ == '__main__':
    main()
