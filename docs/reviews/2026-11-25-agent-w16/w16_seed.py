#!/usr/bin/env python3
"""为 W16 模拟器验收准备数据：设备告警会话、周期回顾会话、带进行中委托的会话。

用法：PETSENSE_ROOT=/tmp/petsense-w16 python3 w16_seed.py
直接写隔离实例的库（与运行中的夹具进程共用同一个 SQLite，WAL 模式可并发写）。
"""
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.environ.get('PETSENSE_ROOT', '/tmp/petsense-w16')
os.environ['PETSENSE_ROOT'] = ROOT
os.environ['PETSENSE_MQTT_DISABLED'] = '1'
sys.path.insert(0, '/Users/yangenhui/.zcode/worktrees/petsense-w16-conversations/petsense_app/server')

from sqlalchemy import select  # noqa: E402

from petsense.agent import conversations, deliver  # noqa: E402
from petsense.db import session_scope, utc_now  # noqa: E402
from petsense.db import configure as db_configure  # noqa: E402
from petsense.db.models import Pet  # noqa: E402
from petsense.db.models.agent import AgentCareTopic, AgentTask, AgentTaskPlan  # noqa: E402

db_configure()
now = utc_now()

with session_scope() as s:
    pet = s.execute(select(Pet)).scalars().first()
    print('宠物:', pet.id, pet.name, 'user', pet.user_id)

    # ── 1. 设备告警会话（关怀主题自带的会话）────────────────────────────
    topic = AgentCareTopic(pet_id=pet.id, kind='device', category='battery', anchor='device:battery',
                            instance=f'device-1-{now.strftime("%Y%m%d%H%M%S")}', status='open',
                            severity='warning', title='项圈电量偏低',
                            detail='项圈电量降到 18%，预计还能用约 6 小时。',
                            evidence=json.dumps([{'at': now.isoformat(), 'value': 18, 'unit': '%'}],
                                                ensure_ascii=False),
                            delivered=0, opened_at=now, updated_at=now)
    s.add(topic)
    s.flush()
    conversation = conversations.for_topic(s, topic, now)
    conversations.sync_care_title(conversation, topic)
    s.flush()
    print('关怀会话 id=%s title=%r topic=%s conv=%s'
          % (conversation.id, conversation.title, topic.id, topic.conversation_id))

    blocks = [{'type': 'text', 'speaker': 'assistant',
               'text': '小白项圈电量偏低（当前 18%），预计还能用约 6 小时，建议尽快充电。'},
              {'type': 'care', 'care': {'kind': 'device', 'category': 'battery',
                                        'title': '项圈电量偏低', 'status': 'open',
                                        'severity': 'warning', 'topicId': topic.id,
                                        'openedAt': now.isoformat(),
                                        'evidence': [{'at': now.isoformat(), 'value': 18, 'unit': '%'}],
                                        'runId': None}}]
    turn, created = deliver.deliver_background_message(
        s, pet.id, 'care:event:%d:open' % topic.id, blocks, conversation_id=conversation.id,
        sources=[], now=now)
    print('交付关怀回合 turn=%s created=%s' % (turn.id, created))

    # ── 2. 周期回顾会话（每条汇总各成一个）──────────────────────────────
    day = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    review = conversations.create_care(s, pet.id, 'review', conversations.review_title('daily', day), now)
    review_blocks = [{'type': 'text', 'speaker': 'assistant',
                      'text': '观察小结：昨天共记录到 3 小时有效活动，比前两天略有减少。'}]
    rturn, rcreated = deliver.deliver_background_message(
        s, pet.id, 'care:review:daily:%d:%s' % (pet.id, day), review_blocks,
        conversation_id=review.id, sources=[], now=now)
    print('回顾会话 id=%s title=%r turn=%s' % (review.id, review.title, rturn.id))

    # ── 3. 一个带进行中委托的会话（删除应被 409 拒绝）────────────────────
    chat = s.execute(select(conversations.AgentConversation).where(
        conversations.AgentConversation.kind == 'chat',
        conversations.AgentConversation.deleted_at.is_(None)).order_by(
        conversations.AgentConversation.id.desc())).scalars().first()
    if chat is None:
        chat = conversations.create_chat(s, pet)[0]
    task = AgentTask(pet_id=pet.id, task_key='w16seedtask0001', current_version=1, status='observing',
                      timezone='Asia/Shanghai', deadline_at=now + timedelta(days=3),
                      effective_from=now, conversation_id=chat.id, proposal_id=None,
                      created_at=now, updated_at=now)
    s.add(task)
    s.flush()
    plan = {'kind': 'activity', 'timezone': 'Asia/Shanghai',
            'startAt': now.isoformat(), 'deadlineAt': (now + timedelta(days=3)).isoformat(),
            'baselineStrategy': 'fact_first'}
    s.add(AgentTaskPlan(task_id=task.id, version=1, payload=json.dumps(plan, ensure_ascii=False),
                         proposal_id=None, created_at=now))
    s.flush()
    print('委托 task=%s status=%s 绑定会话=%s' % (task.id, task.status, chat.id))

print('完成')
