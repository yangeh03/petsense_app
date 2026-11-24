"""把验收夹具真实发出的 WS 帧按顺序录下来，作为客户端回放测试的输入。

客户端的合并规则（app/src/services/agent-chat-turns.ts）平时靠手写的帧做单测；这里的帧来自服务端真实
的推送顺序（领取快照、增量、终态快照、断线重连后的列表拉齐），回放测试
app/tools/agent-group-replay.test.cjs 把它们喂给客户端真实的 store，断言最终状态与服务端历史一致。

夹具（tests/fixtures/group_fixture_server.py）独立启动后运行本脚本，顺序不限：
先发一条不声明协议的旧客户端消息（历史里就有一条投影成只读消息的旧回合），再逐个剧本发送并收帧到
终态；失败剧本追加一次只重试失败那条；断线剧本按 App 的做法重连并拉一次列表。
输出 JSON：初始历史、每个剧本的 POST 响应、帧序列、服务端最终状态。不含会话凭据。

用法（在 server/ 下，用服务端的虚拟环境）：
    python tools/group_fixture_capture.py [端口，默认取 PETSENSE_FIXTURE_PORT，再默认 8094] [输出路径]

更新录制（协议或帧格式变化后）：重启夹具 → 运行本脚本 → 用输出覆盖
app/tools/fixtures/agent-group-frames.json → 在 app/ 下跑 npm test。
"""
import json
import os
import sys
import time

import requests
from simple_websocket import Client, ConnectionClosed

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PETSENSE_FIXTURE_PORT', '8094'))
OUT = sys.argv[2] if len(sys.argv) > 2 else '/tmp/agent-group-frames.json'
BASE = f'http://127.0.0.1:{PORT}'
HEADER, PROTOCOL = 'X-Petsense-Session', 'group-v1'
USER, PASSWORD, PET = 'petsense', '123456', '小白'

# (剧本名, 触发句, 附加字段)：触发句与夹具 SCENARIOS 一致，缺省由剧本表按句命中
CASES = [
    ('dual', '陪我聊两句', {}),
    ('slow', '慢慢说', {}),
    ('fail', '第二位失败', {'target': 'both'}),
    ('drop', '断线一下', {}),
    ('order', '乱序一下', {}),
    ('source', '查一下来源', {}),
    ('search', '联网核实一下', {}),
    ('pet', '想我了吗', {}),
    ('named_pet', '我今天有点累', {'target': 'pet'}),
    ('assistant', '今天走了多久', {'target': 'assistant'}),
    ('clarify', '路由异常', {}),
]


class Recorder:
    """一个像 App 一样收帧的客户端：连接被服务端断开时重连，连上后拉一次列表。"""

    def __init__(self, auth, path, pet_id, key):
        self.auth, self.path, self.pet_id, self.key = auth, path, pet_id, key
        self.sock = None
        self.token = auth[HEADER]

    def connect(self):
        for _ in range(40):
            try:
                self.sock = Client.connect(f'ws://127.0.0.1:{PORT}/api/app/agent/ws'
                                           f'?token={self.token}&protocol={PROTOCOL}')
                return
            except Exception:  # noqa: BLE001 夹具刚断开时端口可能还没就绪
                time.sleep(0.25)
        raise AssertionError('重连失败')

    def listing(self):
        data = requests.get(self.path, headers=self.auth, params={'protocol': PROTOCOL}, timeout=10).json()
        assert data['protocol'] == PROTOCOL and data['dualAgent'] is True, data
        return data['turns']

    def final(self, turn_id):
        return next(turn for turn in self.listing() if turn['id'] == turn_id)

    def send(self, text, suffix, **fields):
        body = {'text': text, 'requestId': f'{self.key}-{suffix}', 'protocol': PROTOCOL, **fields}
        response = requests.post(self.path, headers=self.auth, json=body, timeout=10)
        assert response.status_code == 202, (response.status_code, response.text[:200])
        return {'requestId': body['requestId'], 'body': response.json()}

    def run(self, turn_id, limit=120):
        """收帧到这一轮的终态 turn 帧。"""
        frames, end = [], time.time() + limit
        while time.time() < end:
            try:
                raw = self.sock.receive(timeout=1)
            except ConnectionClosed:
                frames.append({'type': '<closed>'})
                self.connect()
                frames.append({'type': '<open>'})
                frames.append({'type': '<refetch>', 'turns': self.listing()})
                continue
            if raw is None:  # 超时
                continue
            frame = json.loads(raw)
            if frame.get('type') == 'ping':
                continue
            frames.append(frame)
            if frame.get('type') == 'turn' and frame['turn'].get('id') == turn_id \
                    and frame['turn'].get('status') != 'pending':
                return frames
        raise AssertionError(f'回合 {turn_id} 没有在 {limit}s 内结束')


def main():
    health = requests.get(f'{BASE}/__test__/health', timeout=10).json()
    assert (health['ok'], health['enabled'], health['scheduler']) == (True, True, True), f'夹具未就绪：{health}'
    requests.post(f'{BASE}/__test__/reset', timeout=10)
    login = requests.post(f'{BASE}/api/app/auth/login', json={'username': USER, 'password': PASSWORD},
                          timeout=10).json()
    assert login.get('success'), f'登录失败：{login}'
    auth = {HEADER: login['token']}
    pets = requests.get(f'{BASE}/api/app/pets', headers=auth, timeout=10).json()['pets']
    pet_id = next(pet['id'] for pet in pets if pet['name'] == PET)
    path = f'{BASE}/api/app/agent/pets/{pet_id}/turns'
    key = f'capture-{int(time.time())}'
    recorder = Recorder(auth, path, pet_id, key)

    # 旧客户端的一条消息：不声明协议，历史里留下一个没有逐条回复的旧回合
    legacy = requests.post(path, headers=auth, json={'text': '你好', 'requestId': f'{key}-legacy'}, timeout=30)
    assert legacy.status_code == 200 and 'messages' not in legacy.json()['turn'], legacy.text[:200]

    recorder.connect()
    # 初始历史：此刻库里只有那一条旧回合（群聊协议下列表把它投影成只读消息）
    history = {'legacyTurnId': legacy.json()['turn']['id'], 'turns': recorder.listing()}
    assert [turn['id'] for turn in history['turns']] == [history['legacyTurnId']], '夹具不是全新的库，请重启后再录'
    result = []
    try:
        for name, text, fields in CASES:
            post = recorder.send(text, name, **fields)
            frames = [{'type': '<post>', **post}, *recorder.run(post['body']['turn']['id'])]
            time.sleep(0.4)  # 终态帧之后的收尾帧（如果有）也要录到
            frames.extend(drain(recorder.sock))
            case = {'scenario': name, 'turnId': post['body']['turn']['id'], 'frames': frames,
                    'final': recorder.final(post['body']['turn']['id'])}
            if name == 'fail':
                retry = requests.post(f'{path}/{case["turnId"]}/retry', headers=auth, json={}, timeout=10)
                assert retry.status_code == 202, (retry.status_code, retry.text[:200])
                case['retry'] = {'post': retry.json(), 'frames': recorder.run(case['turnId'])}
                time.sleep(0.4)
                case['retry']['frames'].extend(drain(recorder.sock))
                case['retry']['final'] = recorder.final(case['turnId'])
            result.append(case)
            types = [frame['type'] for frame in frames]
            print(f'{name}: 回合 {case["turnId"]}，{len(frames)} 帧 '
                  f'({", ".join(f"{t}×{types.count(t)}" for t in dict.fromkeys(types))})', flush=True)
    finally:
        try:
            recorder.sock.close()
        except Exception:  # noqa: BLE001 连接可能已被夹具断开
            pass
    output = {'history': history, 'cases': result}
    with open(OUT, 'w') as handle:
        json.dump(output, handle, ensure_ascii=False, indent=1)
    print(f'CAPTURE OK → {OUT}', flush=True)


def drain(sock, seconds=0.4):
    """收走这一刻已经在路上的帧（不等待）。"""
    frames, end = [], time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return frames
        try:
            raw = sock.receive(timeout=left)
        except ConnectionClosed:
            return frames
        if raw is None:
            return frames
        frame = json.loads(raw)
        if frame.get('type') != 'ping':
            frames.append(frame)


if __name__ == '__main__':
    try:
        main()
    except AssertionError as exc:
        print(f'CAPTURE FAILED：{exc}', flush=True)
        sys.exit(1)
    os._exit(0)  # simple-websocket 的读线程不是 daemon：结果已落盘，直接结束进程
