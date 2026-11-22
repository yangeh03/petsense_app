"""测试公共：全新数据库（CI 的 PG job 用 PETSENSE_TEST_DATABASE_URL 覆盖）。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense import db


def fresh_db(testcase):
    """为单个测试准备全新数据库与独立运行目录；SQLite 用临时文件，PG 复位重建。

    RUNTIME_ROOT 同步指向临时目录并在用例结束后还原：worker 记忆报告等运行时
    文件写进临时根而不是 repo（server/），避免测试污染工作区。
    """
    from petsense import config
    original_root = config.RUNTIME_ROOT
    holder = tempfile.TemporaryDirectory()
    testcase.addCleanup(holder.cleanup)
    testcase.addCleanup(setattr, config, 'RUNTIME_ROOT', original_root)
    config.RUNTIME_ROOT = Path(holder.name)
    url = os.environ.get('PETSENSE_TEST_DATABASE_URL')
    if url:
        db.configure(url)
        db.reset_schema()
    else:
        db.configure('sqlite:///' + os.path.join(holder.name, 'petsense.db'))
        db.init_db()
