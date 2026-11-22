"""PetMemory 维护工具（W04，agent-w04-spec.md §3.6）。

用法：
  python3 agent_memory.py backfill --pet <id> [--from 2026-11-01] [--to 2026-11-21] [--with-model]
  python3 agent_memory.py report --pet <id>
  python3 agent_memory.py inspect --pet <id> [--kind episode] [--limit 20]

缺省按 PETSENSE_ROOT 定位运行库；--with-model 启用模型小结（需 MINIMAX_API_KEY(_FILE)，
默认 facts-only）。backfill 幂等：已有 active daily 的日子跳过。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense import db  # noqa: E402
from petsense.agent import memory, memory_jobs  # noqa: E402
from petsense.db import utc_now  # noqa: E402
from petsense.db.models import Pet  # noqa: E402


def _configure_db():
    root = os.environ.get('PETSENSE_ROOT')
    if root:
        db.configure(f'sqlite:///{os.path.join(root, "petsense.db")}')
    else:
        db.configure(f'sqlite:///{Path(__file__).resolve().parents[2] / "petsense.db"}')


def _load_pet(pet_id):
    with db.session_scope() as session:
        pet = session.get(Pet, pet_id)
        if pet is None:
            print(f'pet {pet_id} 不存在')
            sys.exit(1)
        return pet


def cmd_backfill(args):
    _configure_db()
    pet = _load_pet(args.pet)
    if not args.with_model:
        # 默认 facts-only：模型小结留给 worker 在有凭据的环境补跑（spec §3.6 降级语义）
        memory_jobs._model_text = lambda system_prompt, user_prompt, max_tokens=500: None
    from datetime import date as date_cls, timedelta
    end_day = args.to or memory.day_text(utc_now() - timedelta(days=1))
    start_day = getattr(args, 'from') or end_day  # from 是保留字，argparse dest 不能直接做属性访问
    results = []
    day = date_cls.fromisoformat(start_day)
    end = date_cls.fromisoformat(end_day)
    while day <= end:
        start, _ = memory.shanghai_day_bounds(day.isoformat())
        with db.session_scope() as session:
            existing = memory.active_daily(session, pet.id, start)
        if existing is None:
            results.append(memory_jobs.generate_daily(pet, day.isoformat(), utc_now()))
        day += timedelta(days=1)
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    print(f'backfill 完成：{len(results)} 天生成、其余已存在跳过')


def cmd_report(args):
    _configure_db()
    pet = _load_pet(args.pet)
    with db.session_scope() as session:
        print(memory.render_report(session, pet))


def cmd_inspect(args):
    _configure_db()
    with db.session_scope() as session:
        result = memory.search(session, args.pet, kinds=[args.kind] if args.kind else None,
                               keyword=args.keyword, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='PetMemory 维护工具（W04）')
    sub = parser.add_subparsers(dest='command', required=True)
    backfill = sub.add_parser('backfill', help='补算日汇总（幂等）')
    backfill.add_argument('--pet', type=int, required=True)
    backfill.add_argument('--from', dest='from', help='起始日（YYYY-MM-DD）')
    backfill.add_argument('--to', dest='to', help='结束日（YYYY-MM-DD）')
    backfill.add_argument('--with-model', action='store_true', help='启用模型小结（默认 facts-only）')
    backfill.set_defaults(func=cmd_backfill)
    report = sub.add_parser('report', help='渲染 markdown 记忆报告到stdout')
    report.add_argument('--pet', type=int, required=True)
    report.set_defaults(func=cmd_report)
    inspect = sub.add_parser('inspect', help='检索记忆条目')
    inspect.add_argument('--pet', type=int, required=True)
    inspect.add_argument('--kind', help='类型过滤')
    inspect.add_argument('--keyword')
    inspect.add_argument('--limit', type=int, default=20)
    inspect.set_defaults(func=cmd_inspect)
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
