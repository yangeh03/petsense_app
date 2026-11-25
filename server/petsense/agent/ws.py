"""Agent WS 直连通道（W01，agent-w01-spec.md §3.6）。

按用户作用域推送 turn 快照与流式增量（累计全文），复用 telemetry/realtime.py 的
flask-sock 实例与连接管理模式；token 鉴权同 /ws（query 参数，RN WebSocket 发不了
自定义 header）。断线后以服务端已保存结果为准（GET turns 拉齐），不补发历史增量。

双 Agent 群聊（docs/agent-dual-agent-chat-design.md §4.3）：连接在 URL 上声明
?protocol=group-v1 后，流式增量按消息 ID/版本推送（message_delta / message / message_status），
turn 快照一律带 messages；未声明的连接继续只收旧的 delta（累计全文，发言人取正在生成的那位）与
旧结构 turn 帧，同一份群聊回合对两类连接都可读。声明只在该账号确实进入双 Agent 时生效：未开放时
这条连接照旧收旧形状的帧（新客户端连到未开放的环境仍能看到流式正文）。

多会话（docs/agent-w16-spec.md §5.5）：回合、流式与消息帧都带 conversationId（取自回合行），客户端据此
把帧送进对应的会话缓存；后台交付、改名、删除之后另推 conversation 帧（会话摘要），列表不必整表重拉。
"""
import json
import threading
import time

from flask import request

from petsense.auth.routes import user_for_token
from petsense.db import session_scope
from petsense.db.models.agent import AgentTurn
from petsense.telemetry.realtime import sock

clients = {}
protocols = {}  # ws → 是否声明群聊协议（只收按消息 ID 推送的帧）


def _send(ws, frame):
    try:
        ws.send(json.dumps(frame, ensure_ascii=False))
    except Exception:
        clients.pop(ws, None)
        protocols.pop(ws, None)


def push(user_id, frame):
    for ws, uid in list(clients.items()):
        if uid == user_id:
            _send(ws, frame)


def _group_view(turn_public):
    """群聊协议连接的回合快照：一律带 messages（后台回信等旧入口推送的快照按持久化消息补齐）。"""
    if 'messages' in turn_public:
        return turn_public
    from petsense.agent import group
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_public.get('id'))
        return group.turn_public(s, turn) if turn is not None else turn_public


def _legacy_turn(turn_public):
    """旧协议连接看到的回合：与旧结构一致（不带 messages；target=both 是群聊新增值，等同未指定）。"""
    if 'messages' not in turn_public and turn_public.get('target') != 'both':
        return turn_public
    projected = {key: value for key, value in turn_public.items() if key != 'messages'}
    if projected.get('target') == 'both':
        projected['target'] = 'auto'
    return projected


def push_turn(user_id, pet_id, turn_public, conversation=None):
    """回合快照推给该用户的全部连接；两类连接按各自协议取形状，内容是同一份持久化结果。

    conversation 是可选的会话摘要：回合落定、列表需要跟着变（标题、预览、活动时间）时由调用方带上，
    客户端据此更新列表；拿不到会话摘要的调用方不必带。
    """
    view = None
    conversation_id = turn_public.get('conversationId')
    for ws, uid in list(clients.items()):
        if uid != user_id:
            continue
        if protocols.get(ws):
            if view is None:
                view = _group_view(turn_public)
            _send(ws, {'type': 'turn', 'petId': pet_id, 'conversationId': conversation_id,
                       'conversation': conversation, 'turn': view})
        else:
            _send(ws, {'type': 'turn', 'petId': pet_id, 'conversationId': conversation_id,
                       'conversation': conversation, 'turn': _legacy_turn(turn_public)})


def push_delta(user_id, pet_id, turn_id, text, speaker, conversation_id=None):
    """旧的流式增量（累计全文）。声明群聊协议的连接只按消息 ID 收增量，不收这一种。"""
    frame = {'type': 'delta', 'petId': pet_id, 'conversationId': conversation_id,
             'turnId': turn_id, 'text': text, 'speaker': speaker}
    for ws, uid in list(clients.items()):
        if uid == user_id and not protocols.get(ws):
            _send(ws, frame)


def _push_group(user_id, frame):
    for ws, uid in list(clients.items()):
        if uid == user_id and protocols.get(ws):
            _send(ws, frame)


def push_message_delta(user_id, pet_id, turn_id, message_id, role, text, version, conversation_id=None):
    """群聊消息的流式增量：text 是这条消息的累计正文，version 是它所属的持久化版本。"""
    _push_group(user_id, {'type': 'message_delta', 'petId': pet_id, 'conversationId': conversation_id,
                          'turnId': turn_id, 'messageId': message_id, 'role': role, 'text': text,
                          'version': version})


def push_message(user_id, pet_id, message, conversation_id=None):
    """群聊消息快照（领取、终态、卡片同步）：message 是 group.message_public 的结果。"""
    _push_group(user_id, {'type': 'message', 'petId': pet_id, 'conversationId': conversation_id,
                          'turnId': message.get('turnId'), 'message': message})


def push_message_status(user_id, pet_id, turn_id, message_id, role, status, conversation_id=None):
    """群聊消息的瞬时状态（如 searching 联网核实中）：状态不是正文，不进消息文本。"""
    _push_group(user_id, {'type': 'message_status', 'petId': pet_id, 'conversationId': conversation_id,
                          'turnId': turn_id, 'messageId': message_id, 'role': role, 'status': status})


def push_conversation(user_id, conversation):
    """会话摘要帧（W16）：所有连接都收，形状与接口返回的会话摘要相同；deletedAt 非空表示已删除。"""
    push(user_id, {'type': 'conversation', 'conversation': conversation})


def start_ping_loop():
    """应用层心跳：检测半死连接并清理僵尸 socket（同 telemetry 模式）。"""
    def loop():
        while True:
            time.sleep(30)
            if not clients:
                continue
            for ws in list(clients):
                _send(ws, {'type': 'ping', 'ts': int(time.time() * 1000)})
    threading.Thread(target=loop, daemon=True, name='agent-ws-ping').start()


def handler(ws):
    token = request.args.get('token', '')
    user = user_for_token(token)
    if not user:
        try:
            ws.send(json.dumps({'type': 'unauthorized'}))
        except Exception:
            pass
        ws.close()
        return
    clients[ws] = user['id']
    from petsense.agent import group
    # 与 client_group 使用同一开关，回退后的连接仍可接收旧路径流式增量。
    protocols[ws] = request.args.get('protocol') == group.PROTOCOL and group.active_for(user)
    try:
        while True:
            if ws.receive() is None:
                break
    except Exception:
        pass
    finally:
        clients.pop(ws, None)
        protocols.pop(ws, None)


sock.route('/api/app/agent/ws')(handler)
