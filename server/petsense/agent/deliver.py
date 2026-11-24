"""后台消息交付边界（W08，agent-w08-spec.md §3；W09 事件/回顾运行共用）。

后台运行（委托检查点、W09 事件）产生的提醒与回信不是对话回合的流式产物：
以稳定 request key 直接落一条完成的 AgentTurn（与交付状态同事务、按唯一键去重），
blocks 自带文本块（旧客户端可读降级）与结构化块（新客户端卡片），提交后按用户推
WS turn 帧；App 关闭期间入库的结果由重开后的 GET turns 补读，WS 与补读按回合 id
收敛不重复。推帧在本模块外（交付事务提交后）由 push_delivered 完成。

多会话（docs/agent-w16-spec.md §5.4）：后台消息必须落在某个会话里——关怀主题自带的会话、周期回顾
每条汇总的会话，或确认该委托的会话。交付之后另推会话摘要帧，客户端的会话列表据此更新。
"""
import json

from sqlalchemy import select

from petsense.db import dialect_insert, iso_z, utc_now
from petsense.db.models import Pet
from petsense.db.models.agent import AgentConversation, AgentTurn
from petsense.agent import conversations


def deliver_background_message(session, pet_id, request_key, blocks, *, conversation_id,
                               sources=None, now=None):
    """落一条后台回合到指定会话（幂等：request_key 已存在时返回既有回合）。返回 (turn, created)。

    用方言 upsert（pet_id, request_id 唯一键）而不是「先查后插」：并发同键写入时
    各得一个幂等结果且只落一条回合，不重复交付。conversation_id 必填。
    """
    if conversation_id is None:
        raise ValueError('后台消息必须指定会话')
    now = now or utc_now()
    values = {'pet_id': pet_id, 'conversation_id': conversation_id, 'request_id': request_key,
              'text': '', 'target': 'assistant', 'reply_to': None,
              'reply': next((block.get('text') for block in blocks
                             if isinstance(block, dict) and block.get('type') == 'text'), ''),
              'speaker': 'assistant',
              'sources': json.dumps(sources or [], ensure_ascii=False),
              'blocks': json.dumps(blocks, ensure_ascii=False),
              'status': 'complete', 'created_at': now}
    stmt = dialect_insert(session, AgentTurn).values(**values)
    stmt = stmt.on_conflict_do_nothing(index_elements=['pet_id', 'request_id'])
    result = session.execute(stmt)
    if result.rowcount == 1:
        turn = session.get(AgentTurn, result.inserted_primary_key[0])
        conversation = session.get(AgentConversation, conversation_id)
        if conversation is not None:
            conversations.touch(conversation, now)
        return turn, True
    existing = session.execute(select(AgentTurn.id).where(
        AgentTurn.pet_id == pet_id, AgentTurn.request_id == request_key)).scalar_one()
    return session.get(AgentTurn, existing), False


def push_delivered(user_id, pet_id, turn):
    """交付事务提交后推 WS turn 帧与会话摘要帧；App 不在线时静默（重开后 GET turns 补读）。"""
    from petsense.agent import ws
    from petsense.agent.routes import public
    ws.push_turn(user_id, pet_id, public(turn))
    conversations.push_summary(user_id, turn.conversation_id)


def pet_owner_id(session, pet_id):
    pet = session.get(Pet, pet_id)
    return pet.user_id if pet else None


def turn_is_background(turn_public):
    """后台回合判定（无主人正文、无图片、已完成）：客户端据此隐藏空气泡。"""
    return turn_public.get('status') == 'complete' and not (turn_public.get('text') or '').strip() \
        and not turn_public.get('images')


__all__ = ['deliver_background_message', 'push_delivered', 'pet_owner_id', 'iso_z']
