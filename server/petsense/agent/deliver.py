"""后台消息交付边界（W08，agent-w08-spec.md §3；W09 事件/回顾运行共用）。

后台运行（委托检查点、W09 事件）产生的提醒与回信不是对话回合的流式产物：
以稳定 request key 直接落一条完成的 AgentTurn（与交付状态同事务、按唯一键去重），
blocks 自带文本块（旧客户端可读降级）与结构化块（新客户端卡片），提交后按用户推
WS turn 帧；App 关闭期间入库的结果由重开后的 GET turns 补读，WS 与补读按回合 id
收敛不重复。推帧在本模块外（交付事务提交后）由 push_delivered 完成。
"""
import json

from sqlalchemy import select

from petsense.db import iso_z, utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import AgentTurn


def deliver_background_message(session, pet_id, request_key, blocks, *, topic_id=None,
                               sources=None, now=None):
    """落一条后台回合（幂等：request_key 已存在时返回既有回合）。返回 (turn, created)。"""
    existing = session.execute(select(AgentTurn).where(
        AgentTurn.pet_id == pet_id, AgentTurn.request_id == request_key)).scalar_one_or_none()
    if existing is not None:
        return existing, False
    text = next((block.get('text') for block in blocks
                 if isinstance(block, dict) and block.get('type') == 'text'), '')
    turn = AgentTurn(pet_id=pet_id, request_id=request_key, text='', target='assistant',
                     topic_id=topic_id, reply_to=None, reply=text, speaker='assistant',
                     sources=json.dumps(sources or [], ensure_ascii=False),
                     blocks=json.dumps(blocks, ensure_ascii=False),
                     status='complete', created_at=now or utc_now())
    session.add(turn)
    session.flush()
    return turn, True


def push_delivered(user_id, pet_id, turn):
    """交付事务提交后推 WS turn 帧；App 不在线时静默（重开后 GET turns 补读）。"""
    from petsense.agent import ws
    from petsense.agent.routes import public
    ws.push_turn(user_id, pet_id, public(turn))


def pet_owner_id(session, pet_id):
    pet = session.get(Pet, pet_id)
    return pet.user_id if pet else None


def turn_is_background(turn_public):
    """后台回合判定（无主人正文、无图片、已完成）：客户端据此隐藏空气泡。"""
    return turn_public.get('status') == 'complete' and not (turn_public.get('text') or '').strip() \
        and not turn_public.get('images')


__all__ = ['deliver_background_message', 'push_delivered', 'pet_owner_id', 'iso_z']
