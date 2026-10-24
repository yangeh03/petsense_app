"""测试公共：全新数据库（CI 的 PG job 用 PETSENSE_TEST_DATABASE_URL 覆盖）。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petsense import db


def fresh_db(testcase):
    """为单个测试准备全新 schema；SQLite 用临时文件，PG 复位重建。"""
    url = os.environ.get('PETSENSE_TEST_DATABASE_URL')
    if url:
        db.configure(url)
        db.reset_schema()
        return
    holder = tempfile.TemporaryDirectory()
    testcase.addCleanup(holder.cleanup)
    db.configure('sqlite:///' + os.path.join(holder.name, 'petsense.db'))
    db.init_db()
