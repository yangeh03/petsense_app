"""Agent WS 直连通道（W01，agent-w01-spec.md §3.6）。

按用户作用域推送 turn 快照与流式增量（累计全文），复用 telemetry/realtime.py 的
flask-sock 实例与连接管理模式；token 鉴权同 /ws（query 参数，RN WebSocket 发不了
自定义 header）。断线后以服务端已保存结果为准（GET turns 拉齐），不补发历史增量。
"""
import json
import threading
import time

from flask import request

from petsense.auth.routes import user_for_token
from petsense.telemetry.realtime import sock

clients = {}


def _send(ws, frame):
    try:
        ws.send(json.dumps(frame, ensure_ascii=False))
    except Exception:
        clients.pop(ws, None)


def push(user_id, frame):
    for ws, uid in list(clients.items()):
        if uid == user_id:
            _send(ws, frame)


def push_turn(user_id, pet_id, turn_public):
    push(user_id, {'type': 'turn', 'petId': pet_id, 'turn': turn_public})


def push_delta(user_id, pet_id, turn_id, text, speaker):
    push(user_id, {'type': 'delta', 'petId': pet_id, 'turnId': turn_id,
                   'text': text, 'speaker': speaker})


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
    try:
        while True:
            if ws.receive() is None:
                break
    except Exception:
        pass
    finally:
        clients.pop(ws, None)


sock.route('/api/app/agent/ws')(handler)
