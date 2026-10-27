"""实时分发：WS 客户端作用域 + SSE 队列 + 心跳线程 + 查询路由。

/ws 鉴权：握手 query 带 token；连接按用户设备集合订阅（认领/解绑即时刷新）。
SSE 面向内部工具，不做归属过滤（后续任务收口）。
"""

import json
import queue
import threading
import time

from flask import Blueprint, Response, request
from flask_sock import Sock
from sqlalchemy import func, select

from petsense import config
from petsense.auth.routes import require_app_user, user_for_token
from petsense.db import iso_z, session_scope
from petsense.db.models import Device, Reading

sock = Sock()
bp = Blueprint('realtime', __name__)
# 连接注册表：ws -> (user_id, 允许设备集合)；设备集合在认领/解绑时重算
ws_clients = {}


def device_ids_for_user(user_id):
    """用户名下设备集合（归属链：devices.owner → 用户）。"""
    with session_scope() as s:
        return set(s.execute(select(Device.device_id)
                             .where(Device.owner_id == user_id)).scalars().all())


def packet_visible(devices, device_id):
    """设备帧只发归属内连接；system 帧（device_id=None）发全体。"""
    return device_id is None or device_id in devices


class Forwarder:
    def __init__(self):
        self.sse_queues = []
        self._lock = threading.Lock()

    def broadcast(self, data):
        with self._lock:
            for q in self.sse_queues:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass

    def add_sse(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self.sse_queues.append(q)
            return q

    def remove_sse(self, q):
        with self._lock:
            try:
                self.sse_queues.remove(q)
            except ValueError:
                pass


fwd = Forwarder()


def broadcast_packet(packet_str, device_id=None):
    """同一份报文发 SSE 队列与 WS 客户端，清理发送失败的僵尸连接。

    device_id 标记报文归属设备，WS 按连接作用域过滤；None=系统帧发全体。
    """
    # Legacy internal SSE has no per-user scope. Never add precise map
    # coordinates to that channel; keep them on the authenticated WS/API.
    internal = packet_str
    if 'uwbPosition' in packet_str:
        try:
            packet = json.loads(packet_str)
            payload = packet.get('payload')
            was_text = isinstance(payload, str)
            payload = json.loads(payload) if was_text else dict(payload)
            payload.pop('uwbPosition', None)
            packet['payload'] = json.dumps(payload) if was_text else payload
            internal = json.dumps(packet)
        except (ValueError, TypeError, AttributeError):
            internal = ''
    if internal:
        fwd.broadcast(internal)
    for client, (_, devices) in list(ws_clients.items()):
        if not packet_visible(devices, device_id):
            continue
        try:
            client.send(packet_str)
        except Exception:
            ws_clients.pop(client, None)


def refresh_scopes():
    """认领/解绑设备后为在线连接重算设备作用域（连接个位数，开销可忽略）。"""
    for client, (user_id, _) in list(ws_clients.items()):
        try:
            ws_clients[client] = (user_id, frozenset(device_ids_for_user(user_id)))
        except Exception:
            ws_clients.pop(client, None)


def start_ws_ping_loop():
    """应用层心跳：让客户端能检测半死连接，同时清理发送失败的僵尸 socket。"""
    def loop():
        while True:
            time.sleep(config.WS_PING_INTERVAL_S)
            if not ws_clients:
                continue
            ping = json.dumps({'type': 'ping', 'ts': int(time.time() * 1000)})
            for client in list(ws_clients):
                try:
                    client.send(ping)
                except Exception:
                    ws_clients.pop(client, None)
    threading.Thread(target=loop, daemon=True, name='ws-ping').start()


@bp.route('/api/stream')
def sse_stream():
    q = fwd.add_sse()
    with session_scope() as s:
        row = s.execute(select(Reading.topic, Reading.payload, Reading.received_at)
                        .order_by(Reading.id.desc()).limit(1)).fetchone()

    def generate():
        try:
            if row:
                yield f'data: {json.dumps({"topic": row.topic, "payload": row.payload, "received_at": iso_z(row.received_at)})}\n\n'
            else:
                yield ': waiting\n\n'
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f'data: {msg}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        except GeneratorExit:
            pass
        finally:
            fwd.remove_sse(q)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Access-Control-Allow-Origin': '*'})


def ws_handler(ws):
    """WS 会话处理：鉴权 → 注册设备作用域 → 保持接收（flask_sock 装饰后的入口在下方）。"""
    token = request.args.get('token', '')
    user = user_for_token(token)
    if not user:
        try:
            # 客户端以此帧识别未登录态；本版 simple_websocket 的 close() 不接受自定义码
            ws.send(json.dumps({'type': 'unauthorized'}))
        except Exception:
            pass
        ws.close()
        return
    ws_clients[ws] = (user['id'], frozenset(device_ids_for_user(user['id'])))
    try:
        while True:
            if ws.receive() is None:
                break
    except Exception:
        pass
    finally:
        ws_clients.pop(ws, None)


sock.route('/ws')(ws_handler)  # 装饰器以副作用注册路由（返回 None），保留原函数供测试直调


@bp.route('/api/latest-full')
def api_latest_full():
    user, error = require_app_user()
    if error:
        return error
    devices = device_ids_for_user(user['id'])
    if not devices:
        return {'count': 0, 'data': []}
    with session_scope() as s:
        rows = s.execute(select(Reading.topic, Reading.payload, Reading.received_at)
                         .where(Reading.device_id.in_(devices))
                         .order_by(Reading.id.desc()).limit(200)).fetchall()
    data = []
    for topic, payload_text, received_at in rows:
        try:
            payload = json.loads(payload_text)
        except Exception:
            payload = {'_raw': payload_text}
        data.append({'topic': topic, 'payload': payload, 'received_at': iso_z(received_at)})
    return {'count': len(data), 'data': data}


@bp.route('/api/stats')
def api_stats():
    with session_scope() as s:
        total, latest = s.execute(select(func.count(), func.max(Reading.received_at))).fetchone()
    return {'total': total, 'latest': iso_z(latest) if latest else None,
            'sse_clients': len(fwd.sse_queues), 'ws_clients': len(ws_clients)}
