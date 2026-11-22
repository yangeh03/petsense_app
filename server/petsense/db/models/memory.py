"""PetMemory 统一存储（W04，agent-w04-spec.md §3.2–§3.5）。

分层写入：episode/owner_event/habit_candidate/session_takeaway 对话自主写，
daily/weekly/insight_card 由维护 worker 写，preference 确认写（W03 后），
change_event/task_result 预留（W08/W09）。带来源、版本与 supersede 链，
可回滚可审计；summary 建有 FTS5 trigram 索引（SQLite）。
"""
from sqlalchemy import Column, ForeignKey, Integer, String, Text

from petsense.db.base import Base, UTCDatetime


class PetMemory(Base):
    __tablename__ = 'pet_memories'
    id = Column(Integer, primary_key=True)
    pet_id = Column(Integer, ForeignKey('pets.id'), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    subject = Column(String(16), nullable=False, default='pet')  # pet / owner；其他主体不写入
    start_at = Column(UTCDatetime, nullable=True)  # 发生时间；未知则 NULL，不用接收时间冒充
    end_at = Column(UTCDatetime, nullable=True)
    generated_at = Column(UTCDatetime, nullable=False)
    facts = Column(Text, nullable=False, default='{}')  # 结构化汇总（JSON）
    summary = Column(Text, nullable=True)  # 文字表达（FTS5 索引列）
    source_refs = Column(Text, nullable=False, default='[]')  # [{type:'turn'|'run'|'observations', ...}]
    support_snapshot = Column(Text, nullable=True)
    source_type = Column(String(16), nullable=False, default='agent')  # agent / owner / worker
    write_level = Column(String(16), nullable=False, default='autonomous')  # autonomous / confirmed（W03 预留）
    confirmation = Column(Text, nullable=True)  # 确认状态（W03 预留，本包恒 NULL）
    certainty = Column(String(16), nullable=False, default='stated')  # stated / tentative
    version = Column(Integer, nullable=False, default=1)
    supersedes_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default='active')  # active / superseded / retired
    created_at = Column(UTCDatetime, nullable=False)
