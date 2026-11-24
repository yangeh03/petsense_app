"""双 Agent 群聊验收夹具的冒烟检查：对已经启动的夹具走一遍 iOS 验收会经过的接口。

夹具（tests/fixtures/group_fixture_server.py）独立启动后先跑本脚本，确认隔离后端可用再去模拟器：
鉴权、群聊协议声明、两条真实 WebSocket（一条声明 group-v1，一条不声明）、第二位失败与只重试失败的
那条、剧本覆盖与撤销、旧客户端兼容、历史、幂等回放、消息级引用、出字途中断线与重连。全部通过退出码 0；
任何一步不符即失败并停在该步。本脚本只发请求。

用法（在 server/ 下，用服务端的虚拟环境）：
    python tools/group_fixture_smoke.py [端口，默认取 PETSENSE_FIXTURE_PORT，再默认 8094]
"""
import json
import os
import sys
import time

import requests
from simple_websocket import Client, ConnectionClosed

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PETSENSE_FIXTURE_PORT', '8094'))
BASE = f'http://127.0.0.1:{PORT}'
HEADER = 'X-Petsense-Session'
PROTOCOL = 'group-v1'
USER, PASSWORD, PET = 'petsense', '123456', '小白'


def passed(name):
    print(f'[ok] {name}', flush=True)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def connect(token, protocol=None):
    url = f'ws://127.0.0.1:{PORT}/api/app/agent/ws?token={token}' + (f'&protocol={protocol}' if protocol else '')
    return Client.connect(url)


def drain(sock, seconds=0.4):
    frames, end = [], time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return frames
        try:
            frame = sock.receive(timeout=left)  # 超时返回 None；连接被关闭抛 ConnectionClosed
        except ConnectionClosed:
            frames.append({'type': '<closed>'})
            return frames
        if frame is None:
            return frames
        frames.append(json.loads(frame))


def settled(sock, turn_id, limit=60):
    """收帧直到这一轮的终态 turn 帧，返回途中收到的全部帧。"""
    frames, end = [], time.time() + limit
    while time.time() < end:
        for frame in drain(sock, 1):
            frames.append(frame)
            if frame.get('type') == 'turn' and frame['turn'].get('id') == turn_id \
                    and frame['turn'].get('status') != 'pending':
                return frames + drain(sock, 0.3)
    raise AssertionError(f'回合 {turn_id} 没有在 {limit}s 内结束（调度线程没起来？）')


def final_turn(frames):
    return [frame for frame in frames if frame.get('type') == 'turn'][-1]['turn']


def deltas(frames):
    return [frame for frame in frames if frame.get('type') == 'message_delta']


def wait_final(path, auth, turn_id, limit=60):
    """轮询历史直到这一轮结束（连接断开期间只能靠列表拿终态）。"""
    end = time.time() + limit
    while time.time() < end:
        listing = requests.get(path, headers=auth, params={'protocol': PROTOCOL}, timeout=10).json()
        turn = next(item for item in listing['turns'] if item['id'] == turn_id)
        if turn['status'] != 'pending':
            return turn
        time.sleep(0.3)
    raise AssertionError(f'回合 {turn_id} 没有在 {limit}s 内结束')


def growing(frames):
    """同一条消息的累计正文只增不减。"""
    seen = {}
    for frame in deltas(frames):
        own = seen.setdefault(frame['messageId'], [])
        if own and len(frame['text']) < own[-1]:
            return False
        own.append(len(frame['text']))
    return True


def main():
    health = requests.get(f'{BASE}/__test__/health', timeout=10).json()
    ready = (health['ok'], health['enabled'], health['scheduler'], health['protocol'])
    expect(ready == (True, True, True, PROTOCOL), f'夹具未就绪：{health}')
    count = len(requests.get(f'{BASE}/__test__/scenarios', timeout=10).json()['scenarios'])
    passed(f'控制接口可用：{count} 个剧本，数据目录 {health["root"]}')

    login = requests.post(f'{BASE}/api/app/auth/login', json={'username': USER, 'password': PASSWORD}, timeout=10).json()
    expect(login.get('success'), f'登录失败：{login}')
    auth = {HEADER: login['token']}
    pets = requests.get(f'{BASE}/api/app/pets', headers=auth, timeout=10).json()['pets']
    pet_id = next(pet['id'] for pet in pets if pet['name'] == PET)
    path = f'{BASE}/api/app/agent/pets/{pet_id}/turns'
    key = f'smoke-{int(time.time())}'
    passed(f'账号 {USER} 登录，宠物「{PET}」(id={pet_id})')

    anonymous = requests.post(path, timeout=10, json={'text': '你好', 'requestId': f'{key}-noauth',
                                                      'protocol': PROTOCOL})
    expect(anonymous.status_code == 401, '未登录发送没有返回 401')
    passed('未登录发送 → 401')

    def send(text, suffix, **fields):
        response = requests.post(path, headers=auth, timeout=10, json={
            'text': text, 'requestId': f'{key}-{suffix}', 'protocol': PROTOCOL, **fields})
        expect(response.status_code == 202, f'发送「{text}」没有返回 202：{response.status_code} {response.text[:200]}')
        return response.json()['turn']['id']

    def trace(turn_id):
        return requests.get(f'{BASE}/__test__/trace', params={'turn': turn_id}, timeout=10).json()

    def retry(turn_id):
        return requests.post(f'{path}/{turn_id}/retry', headers=auth, json={}, timeout=10)

    group_ws, legacy_ws = connect(login['token'], PROTOCOL), connect(login['token'])
    try:
        # 1) 第二位失败 → 只重试失败的那条
        fail_id = send('第二位失败', 'fail', target='both')
        frames, legacy_frames = settled(group_ws, fail_id), settled(legacy_ws, fail_id)
        pet, assistant = (message['id'] for message in final_turn(frames)['messages'])
        expect([(m['role'], m['status']) for m in final_turn(frames)['messages']]
               == [('pet', 'complete'), ('assistant', 'failed')], '失败剧本的终态不符')
        expect({frame['messageId'] for frame in deltas(frames)} == {pet, assistant}, '增量帧的消息 ID 不符')
        expect(all('messages' not in frame['turn'] for frame in legacy_frames if frame.get('type') == 'turn')
               and any(frame.get('type') == 'delta' for frame in legacy_frames), '旧协议连接收到的不是旧形状的帧')
        expect(retry(fail_id).status_code == 202, '重试没有返回 202')
        frames = settled(group_ws, fail_id)
        settled(legacy_ws, fail_id)  # 两条连接各自收齐这一轮，下一轮的帧才不会与上一轮的残余混在一起
        expect({frame['messageId'] for frame in deltas(frames)} == {assistant}, '重试时爱宠那条也被重发了')
        messages = final_turn(frames)['messages']
        expect([m['status'] for m in messages] == ['complete', 'complete'], '重试后的终态不符')
        again = retry(fail_id)
        expect((again.status_code, again.json().get('code')) == (409, 'nothing_to_retry'), '全部完成后重试应为 409')
        passed('第二位失败 → 只重试失败的那条 → 再重试 409')

        # 2) 双答：未选指向时由路由决定两位都发言
        dual_id = send('陪我聊两句', 'dual')
        frames = settled(group_ws, dual_id)
        legacy_frames = settled(legacy_ws, dual_id)
        messages = final_turn(frames)['messages']
        expect([m['role'] for m in messages] == ['pet', 'assistant'], '双答终态不是爱宠 + 照料助手')
        expect(list(dict.fromkeys(frame['messageId'] for frame in deltas(frames))) == [m['id'] for m in messages],
               '增量的先后顺序不是爱宠先、照料助手后')
        expect(growing(frames), '同一条消息的累计正文出现了回退')
        expect(not any(frame.get('type') == 'delta' for frame in frames), '群聊连接不应收到旧的 delta 帧')
        expect([frame['speaker'] for frame in legacy_frames if frame.get('type') == 'delta'][:1] == ['pet']
               and 'messages' not in final_turn(legacy_frames), '旧协议连接看到的不是旧的累计全文')
        passed('双答：爱宠先发、照料助手后发，增量按消息 ID 推送且累计只增不减')

        # 3) 剧本覆盖与撤销
        requests.post(f'{BASE}/__test__/scenario', json={'name': 'search', 'sticky': True}, timeout=10)
        override_id = send('随便说点什么', 'override')
        settled(group_ws, override_id), settled(legacy_ws, override_id)
        result = trace(override_id)
        expect(result['scenario'] == 'search' and result['messages'][0]['sources'], '常驻覆盖没有生效')
        requests.post(f'{BASE}/__test__/reset', timeout=10)
        override = requests.get(f'{BASE}/__test__/health', timeout=10).json()['override']
        expect(override == {'name': None, 'sticky': False}, f'reset 后覆盖仍在：{override}')
        passed('剧本覆盖（sticky）与 reset')

        # 4) 旧客户端：不声明协议
        legacy = requests.post(path, headers=auth, timeout=10, json={'text': '你好', 'requestId': f'{key}-legacy'})
        expect(legacy.status_code == 200 and 'messages' not in legacy.json()['turn'], '旧客户端拿到的不是旧结构')
        refused = requests.post(path, headers=auth, timeout=10, json={
            'text': '你好', 'requestId': f'{key}-legacy-2', 'target': 'both'})
        expect((refused.status_code, refused.json().get('code')) == (400, 'dual_agent_unavailable'),
               '旧客户端选「两位」应返回 400 dual_agent_unavailable')
        passed('旧客户端：200 旧结构；选「两位」→ 400 dual_agent_unavailable')

        # 5) 历史、幂等回放与消息级引用
        listing = requests.get(path, headers=auth, params={'protocol': PROTOCOL}, timeout=10).json()
        expect((listing['protocol'], listing['dualAgent']) == (PROTOCOL, True), '群聊协议的历史没有带协议标记')
        read_only = [m for t in listing['turns'] for m in t['messages'] if m['readOnly']]
        expect(read_only and all(m['id'].startswith('t') for m in read_only), '旧回合没有投影成只读消息')
        old = requests.get(path, headers=auth, timeout=10).json()
        expect(all('messages' not in t and t['target'] != 'both' for t in old['turns']), '旧协议的历史带上了群聊字段')
        replay = requests.post(path, headers=auth, timeout=10, json={
            'text': '第二位失败', 'requestId': f'{key}-fail', 'protocol': PROTOCOL, 'target': 'both'})
        expect(replay.status_code == 200 and replay.json()['turn']['id'] == fail_id, '同一 requestId 重发没有回放原回合')
        quote_id = send('你刚才说的再讲讲', 'quote', replyTo=override_id, replyToMessage=result['messages'][0]['id'])
        settled(group_ws, quote_id), settled(legacy_ws, quote_id)
        route_input = trace(quote_id)['runs'][0]['messages'][0]['text']
        quoted = [line for line in route_input.split('\n') if line.startswith('被引用消息')]
        expect(len(quoted) == 1 and quoted[0].startswith('被引用消息：【照料助手】刚核实过'),
               f'路由输入里的引用行不符：{quoted}')
        passed('历史（两种协议）、幂等回放、引用精确到消息并进入路由输入')

        # 6) 断线：照料助手出字途中服务端断开连接（该账号的连接全部被断开）→ 重连 → 按列表拉齐
        drop_id = send('断线一下', 'drop')
        frames, end = [], time.time() + 60
        while time.time() < end and not (frames and frames[-1]['type'] == '<closed>'):
            frames.extend(drain(group_ws, 2))
        expect(frames and frames[-1]['type'] == '<closed>', '夹具没有断开这条连接')
        streamed = [frame for frame in deltas(frames) if frame['role'] == 'assistant']
        expect(len(streamed) == 2, f'断开前应刚好收到照料助手的两段增量：{len(streamed)}')
        messages = wait_final(path, auth, drop_id)['messages']
        expect([(m['role'], m['status']) for m in messages] == [('pet', 'complete'), ('assistant', 'complete')],
               '断开后这一轮没有照常跑完')
        assistant = messages[1]
        expect(assistant['id'] == streamed[-1]['messageId'], '断开前后的消息 ID 不一致')
        expect(assistant['text'].startswith(streamed[-1]['text']) and len(assistant['text']) > len(streamed[-1]['text']),
               '断开前收到的增量与最终正文接不上')
        group_ws = connect(login['token'], PROTOCOL)
        after_id = send('想我了吗', 'after-drop')
        expect([m['role'] for m in final_turn(settled(group_ws, after_id))['messages']] == ['pet'],
               '重连后的连接收不到新一轮的帧')
        passed('断线：对端收到关闭、这一轮照常跑完、重连后收得到新一轮的帧')
    finally:
        for sock in (group_ws, legacy_ws):
            try:
                sock.close()
            except Exception:  # noqa: BLE001 连接可能已被服务端关闭
                pass
    print('FIXTURE SMOKE OK', flush=True)


if __name__ == '__main__':
    try:
        main()
    except AssertionError as exc:
        print(f'FIXTURE SMOKE FAILED：{exc}', flush=True)
        sys.exit(1)
    os._exit(0)  # simple-websocket 的读线程不是 daemon：结果已打印，直接结束进程
