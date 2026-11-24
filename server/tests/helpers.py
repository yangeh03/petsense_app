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


def new_conversation(pet_id, care_kind=None, title=''):
    """给测试建一个会话并返回 id（W16 起回合必须属于某个会话）。不给 care_kind 时建主人会话。"""
    from petsense.agent import conversations
    from petsense.db.models import Pet
    with db.session_scope() as s:
        if care_kind is None:
            row, _created = conversations.create_chat(s, s.get(Pet, pet_id))
        else:
            row = conversations.create_care(s, pet_id, care_kind, title)
        return row.id


class TurnClient:
    """带会话的测试客户端（W16 起取历史与发消息都必须指定会话）。

    第一次用到时给这只宠物建一个主人会话：之后对 …/turns 的 GET 自动带上 conversationId，
    POST 的正文自动补上它（正文里显式给了就用显式的）。既有的用例因此不必逐处改写；
    需要直接造回合的用例读 conversation_id，拿的是同一个会话。raw 是被包装的原始客户端，
    用来验证「不带会话」这类包装器会自动补上的行为。会话在构造时就建好：有些用例在事务里
    发请求，那时再开事务建会话会撞上「session_scope 不可嵌套」。
    """

    def __init__(self, client, pet_id):
        self.raw = client
        self.conversation_id = new_conversation(pet_id)

    @staticmethod
    def _is_turns(path):
        return path.partition('?')[0].rstrip('/').endswith('/turns')

    def _with_conversation(self, path, kwargs):
        """GET 的会话参数：调用方给了 query_string 就并进去，没有就拼在路径上。"""
        if not self._is_turns(path):
            return path, kwargs
        query = kwargs.get('query_string')
        if isinstance(query, dict):
            kwargs = {**kwargs, 'query_string': {'conversationId': self.conversation_id, **query}}
        elif isinstance(query, list):
            kwargs = {**kwargs, 'query_string': query + [('conversationId', self.conversation_id)]}
        elif query is not None:
            kwargs = {**kwargs, 'query_string': '%s&conversationId=%d' % (query, self.conversation_id)}
        else:
            base, _, existing = path.partition('?')
            path = base + '?' + '&'.join(
                part for part in (existing, 'conversationId=%d' % self.conversation_id) if part)
        return path, kwargs

    def get(self, path, *args, **kwargs):
        path, kwargs = self._with_conversation(path, kwargs)
        return self.raw.get(path, *args, **kwargs)

    def post(self, path, *args, json=None, **kwargs):
        if isinstance(json, dict) and self._is_turns(path):
            json = {'conversationId': self.conversation_id, **json}
        return self.raw.post(path, *args, json=json, **kwargs)

    def __getattr__(self, name):
        return getattr(self.raw, name)
