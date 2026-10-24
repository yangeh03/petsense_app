"""实时分发：WS 客户端集合 + SSE 队列 + 心跳线程 + 查询路由。"""

import json
import queue
import threading
import time

from flask import Blueprint, Response, request
from flask_sock import Sock

from petsense import config, db

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


def broadcast_packet(packet_str):
    """同一份报文发 SSE 队列与全部 WS 客户端，清理发送失败的僵尸连接。"""
    fwd.broadcast(packet_str)
    for client in ws_clients.copy():
        try:
            client.send(packet_str)
        except Exception:
            ws_clients.discard(client)


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

    def generate():
        try:
            db.cur.execute('SELECT topic, payload, received_at FROM readings ORDER BY id DESC LIMIT 1')
            row = db.cur.fetchone()
            if row:
                yield f'data: {json.dumps({"topic": row[0], "payload": row[1], "received_at": row[2]})}\n\n'
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
    db.cur.execute('SELECT topic, payload, received_at FROM readings ORDER BY id DESC LIMIT 200')
    rows = []
    for r in db.cur.fetchall():
        try:
            p = json.loads(r[1])
        except Exception:
            p = {'_raw': r[1]}
        rows.append({'topic': r[0], 'payload': p, 'received_at': r[2]})
    return {'count': len(rows), 'data': rows}


@bp.route('/api/stats')
def api_stats():
    db.cur.execute('SELECT COUNT(*), MAX(received_at) FROM readings')
    cnt, latest = db.cur.fetchone()
    return {'total': cnt, 'latest': latest,
            'sse_clients': len(fwd.sse_queues), 'ws_clients': len(ws_clients)}
