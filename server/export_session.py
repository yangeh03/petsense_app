"""Export a session without fabricating missing samples or overwriting previous exports."""
import argparse
import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3

def export(db_path, session, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(db_path)
    rows = db.execute('SELECT device,boot,seq,mono_us,utc_us,received_us,document FROM frames WHERE session=? ORDER BY boot,seq', (session,)).fetchall()
    previous = {}
    gaps = []
    fields = ['device', 'boot_id', 'seq', 'mono_us', 'utc_us', 'beijing_time', 'cloud_received_us',
              'transport_delay_us', 'ax_g', 'ay_g', 'az_g', 'gx', 'gy', 'gz',
              'imu_valid', 'health_json', 'audio_features_json', 'clock_uncertainty_us']
    with (output / 'samples.jsonl').open('w', encoding='utf8') as raw, (output / 'samples.csv').open('w', encoding='utf8', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        for device, boot, seq, mono, utc, received, document in rows:
            frame = json.loads(document)
            raw.write(json.dumps({'device': device, 'cloud_received_us': received, 'sample': frame}, ensure_ascii=False) + '\n')
            key = (device, boot)
            if key in previous and seq != previous[key] + 1:
                gaps.append({'device': device, 'boot': boot, 'after': previous[key], 'before': seq})
            previous[key] = seq
            imu = frame.get('imu', {})
            accel, gyro = imu.get('accel', [None]*3), imu.get('gyro', [None]*3)
            writer.writerow(dict(zip(fields, [device, boot, seq, mono, utc,
                datetime.fromtimestamp(utc/1e6, timezone(timedelta(hours=8))).isoformat() if utc else '',
                received, received-utc if utc else '', *accel, *gyro, imu.get('ok'),
                json.dumps(frame.get('health')), json.dumps(frame.get('audio')),
                frame.get('clock_uncertainty_us')])))
    videos = [json.loads(row[0]) for row in db.execute('SELECT document FROM videos WHERE session=?', (session,))]
    (output / 'video_annotations.json').write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding='utf8')
    manifest = {'session': session, 'samples': len(rows), 'sequence_gaps': gaps,
                'missing_utc': sum(row[4] is None for row in rows),
                'complete': False, 'note': 'Consult SD reconciliation; no interpolation or missing-value replacement was performed.',
                'video_files': [str(Path(db_path).parent / session / v['id'] / 'video.mov') for v in videos]}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    db.close()
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='collection_data/collection.sqlite3')
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.db, args.session, args.output), indent=2))
