"""AgentRun 轨迹回放（W01，agent-w01-spec.md §3.5）。

只读打印一次运行的完整判断与工具路径，供调试、验收取证与「为什么这样回复」的核对。
用法：
  python3 agent_replay.py <run_id> [--db /path/to/data.db]
  缺省 --db 取 PETSENSE_ROOT（或包上一级运行目录）下的 data.db。
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def default_db():
    root = os.environ.get('PETSENSE_ROOT')
    if not root:
        root = str(Path(__file__).resolve().parents[2])
    return os.path.join(root, 'data.db')


def main():
    parser = argparse.ArgumentParser(description='回放一次 AgentRun 轨迹')
    parser.add_argument('run_id', type=int)
    parser.add_argument('--db', default=default_db())
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    row = connection.execute('SELECT * FROM agent_runs WHERE id = ?', (args.run_id,)).fetchone()
    if row is None:
        print(f'run {args.run_id} 不存在（库：{args.db}）')
        return 1
    print(f'# AgentRun {row["id"]}  pet={row["pet_id"]}  turn={row["turn_id"]}  '
          f'form={row["form"]}  status={row["status"]}  created={row["created_at"]}')
    if row['error']:
        print(f'error: {row["error"]}')
    conclusion = json.loads(row['conclusion'])
    print('\n== 结论 ==')
    print(json.dumps(conclusion, ensure_ascii=False, indent=2))
    print('\n== 工具调用 ==')
    for call in json.loads(row['tool_summary']):
        print(json.dumps(call, ensure_ascii=False))
    print('\n== 消息序列（截断存储） ==')
    for message in json.loads(row['messages']):
        print(json.dumps(message, ensure_ascii=False)[:600])
    print('\n== 预算 ==')
    print(json.loads(row['budget']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
