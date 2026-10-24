"""实时分发：WS 客户端集合 + SSE 队列 + 心跳线程 + 查询路由。"""

import json
import queue
import threading
import time

from flask import Blueprint, Response, request
from flask_sock import Sock
from sqlalchemy import func, select

from petsense import config
from petsense.db import iso_z, session_scope
from petsense.db.models import Reading

sock = Sock()
bp = Blueprint('realtime', __name__)
ws_clients = set()


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

    device_id 标记报文归属设备（WS 按连接作用域过滤，T4 实装；None=系统帧发全体）。
    SSE 面向内部工具，始终全量。
    """
    fwd.broadcast(packet_str)
    for client in ws_clients.copy():
        try:
            client.send(packet_str)
        except Exception:
            ws_clients.discard(client)


def refresh_scopes():
    """认领/解绑设备后刷新在线连接的订阅作用域（ws 过滤实装前的占位）。"""
    return None


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
                    ws_clients.discard(client)
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


@sock.route('/ws')
def ws_route(ws):
    ws_clients.add(ws)
    try:
        while True:
            if ws.receive() is None:
                break
    except Exception:
        pass
    finally:
        ws_clients.discard(ws)


@bp.route('/api/latest-full')
def api_latest_full():
    with session_scope() as s:
        rows = s.execute(select(Reading.topic, Reading.payload, Reading.received_at)
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
