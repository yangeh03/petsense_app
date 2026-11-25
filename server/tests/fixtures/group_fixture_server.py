"""双 Agent 群聊验收夹具：一次性隔离后端，模型换成确定性的分段剧本。

用途（docs/agent-dual-agent-chat-design.md §6–§7）：让 iOS 模拟器里的 App 对着可控的分段输出
走一遍「路由 → 逐条消息 → WS 帧 → 气泡」，并用控制接口取证：谁被路由到、每条消息的
ID/角色/版本/状态、按顺序发出的帧。只用于验收：不连真实模型、不读模型凭据、不进生产路径
（生产代码不 import 本文件）。

启动（在 server/ 下；数据写进新建的临时目录，不碰任何现有库）：
  PETSENSE_MQTT_DISABLED=1 python tests/fixtures/group_fixture_server.py
  可选：PETSENSE_FIXTURE_PORT=8094   PETSENSE_FIXTURE_SEED=0（不预置账号）   PETSENSE_FIXTURE_ROOT=<目录>
启动使用双 Agent 默认行为，预置验收账号
petsense / 123456 与一只宠物「小白」，只监听 127.0.0.1（模拟器与宿主共用网络）。

App：EXPO_PUBLIC_API_BASE_URL=http://127.0.0.1:<端口> npx expo start --ios
     （WS 与 API 同源；改了 EXPO_PUBLIC_* 必须让 App 重新拉包）

触发方式：在 App 的伙伴页直接发送 /__test__/scenarios 里的「触发句」；或先固定剧本：
  POST /__test__/scenario  {"name": "slow"}                 只作用于下一条消息
  POST /__test__/scenario  {"name": "slow", "sticky": true}  一直生效，直到 {"name": null}
  GET  /__test__/scenarios   剧本清单（触发句、路由输出、应观察到的现象）
  GET  /__test__/trace?turn=<回合 id>   路由记录 + 消息 + 运行轨迹 + 逐帧日志；?latest=1 取最近一个
  POST /__test__/reset       清空帧日志与剧本覆盖
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def prepare_environment():
    """独立启动时的环境：全新的数据目录、不连 MQTT、开启双 Agent。

    运行目录在 import petsense 时读取，所以必须在导入之前调用；作为模块被测试导入时不调用，
    各用例自己设置开关。
    """
    if not os.environ.get('PETSENSE_ROOT'):
        os.environ['PETSENSE_ROOT'] = os.environ.get('PETSENSE_FIXTURE_ROOT') or tempfile.mkdtemp(
            prefix='petsense-group-fixture-')
    os.environ['PETSENSE_MQTT_DISABLED'] = '1'
    os.environ.pop('PETSENSE_SCHEDULER_DISABLED', None)  # 群聊回合由调度线程触发
    os.environ.pop('PETSENSE_DUAL_AGENT_ENABLED', None)


if __name__ == '__main__':
    prepare_environment()

from flask import request  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from petsense import config  # noqa: E402
from petsense.agent import group, loop, model, runs, ws  # noqa: E402
from petsense.db import session_scope  # noqa: E402
from petsense.db.models.agent import AgentMessage, AgentRun, AgentTurn  # noqa: E402

SEED_USER, SEED_PASSWORD, SEED_PET = 'petsense', '123456', '小白'
LEGACY_STANCE = '{"scope":"allowed","intent":"social","speaker":"assistant"}\n'
# 假想的网页来源：只在夹具里出现，域名一眼可辨，不会被当成真实核对过的页面
FIXTURE_WEB = {'url': 'https://example.com/petsense-fixture/care-note',
               'title': '夹具来源页（非真实网页）', 'snippet': '这段摘要来自验收夹具，不是真实搜索结果。'}

# 一段 = 一次流式回调后的可见正文；delay 是相邻两段之间的间隔（秒），让分段在界面上看得见
SCENARIOS = {
    'dual': {
        'title': '双答：爱宠先发一条，照料助手再发一条',
        'trigger': '陪我聊两句',
        'target': 'both',
        'pet': ['想你啦！', '今天有乖乖等你回家，', '走动的事让照料助手告诉你。'],
        'assistant': ['我查了今天的记录：', '这项圈还没有上传走动数据，', '所以暂时给不出今天走了多久。'],
        'delay': 0.4,
        'expect': ['第一条气泡属于爱宠：首帧起头像与名字就是爱宠，正文不带【爱宠】标记',
                   '爱宠发完之后才出现照料助手的气泡，两条各自保留到结束',
                   '爱宠正文里的「照料助手」是普通文字，不会把这条气泡拆成两个人',
                   '两条都结束后整轮完成；trace 的 route.plan 是 pet → assistant'],
    },
    'handoff': {
        'title': '默认模式转交：爱宠先说一句，照料助手接着答专业部分',
        'trigger': '它今天走了多久',
        'target': 'auto',
        'handoff': True,
        'pet': ['这个我不懂，', '让照料助手帮你看看。'],
        'assistant': ['我查了今天的记录：', '这项圈还没有上传走动数据，', '所以暂时给不出今天走了多久。'],
        'delay': 0.4,
        'expect': ['爱宠那条先出现并完成：它只说一句陪伴话，没有任何数据',
                   '爱宠转交之后才出现照料助手的气泡，专业答案只在那一条里',
                   '照料助手的输入里带「爱宠已经把需要查证的部分交给你」（trace 的 assistant 运行可见）',
                   'trace 的 pet 运行 tool_summary 里有 handoff_to_assistant（allowed=true）'],
    },
    'slow': {
        'title': '第二位慢：爱宠已完成，照料助手逐段慢慢出字',
        'trigger': '慢慢说',
        'target': 'both',
        'pet': ['我在呢～', '你慢慢说。'],
        'assistant': ['狗狗一天睡够 12 到 14 小时都算正常。', '刚睡醒时精神好、', '吃喝照常，',
                      '就不用太担心；', '如果整天蔫着不吃，再联系兽医。'],
        'delay': 1.6,
        'expect': ['爱宠那条先停在完成态（不再出字），照料助手那条仍显示生成中',
                   '等待期间上翻或切后台再回来，两条气泡的 ID 与顺序不变',
                   '两位各自独立：照料助手那条不引用爱宠的话，只回答专业部分'],
    },
    'fail': {
        'title': '第二位失败：爱宠成功，照料助手失败，可只重试失败的那条',
        'trigger': '第二位失败',
        'target': 'both',
        'pet': ['我在呢，', '让照料助手帮你查。'],
        'assistant': ['这次查到了：', '今天没有走动记录。'],
        'fail_first': {'assistant': True},
        'delay': 0.4,
        'expect': ['爱宠那条正常完成并保留；照料助手那条显示失败说明与重试入口，中途出过的半截正文被撤掉',
                   '整轮结束后爱宠那条不重发、不重复',
                   '点重试后只有照料助手那条重新出字并完成，消息 ID 不变、版本递增（看 trace 的 frames）',
                   'trace 的 runs 里 assistant 先有一条 failed，重试后新增一条 complete'],
    },
    'drop': {
        'title': '断线：照料助手出字途中服务端主动断开 WS',
        'trigger': '断线一下',
        'target': 'both',
        'pet': ['别担心，', '问问照料助手吧。'],
        'assistant': ['先说结论：', '多数情况下不用紧张，', '观察一天，', '没有好转再去医院。'],
        'drop_after': {'role': 'assistant', 'deltas': 2},
        'delay': 1.2,
        'expect': ['照料助手第二条增量之后连接被断开，App 自动重连',
                   '重连后这一轮继续出字或直接到达终态；中断的那条不丢、不重复、不变成两条',
                   '服务端这一轮照常跑完（trace 里两条消息都是 complete）'],
    },
    'order': {
        'title': '重复与乱序帧：服务端在真实帧之间插入旧版本与重复帧',
        'trigger': '乱序一下',
        'target': 'both',
        'pet': ['我来陪你。', '数据让照料助手说。'],
        'assistant': ['查到了：', '今天暂时没有走动数据，', '有数据时我会在这一条里告诉你。'],
        'inject': True,
        'delay': 0.6,
        'expect': ['气泡文字只增不减、不闪回旧内容（旧版本帧被忽略）',
                   '两条气泡始终各一个，头像与名字不跳变',
                   'trace 的 frames 里有 injected=true 的注入帧（版本不高于它前后的真实帧），正文仍是最终版本'],
    },
    'source': {
        'title': '查询与来源卡：照料助手先查观测再回答，来源卡只挂在照料助手那条',
        'trigger': '查一下来源',
        'target': 'both',
        'pet': ['我玩得挺开心的！', '走了多久要问照料助手。'],
        'assistant': ['我查了今天零点到现在的记录：', '没有有效观测，', '所以不能说它走了多久。'],
        'tool': 'query_observations',
        'delay': 0.5,
        'expect': ['来源卡只出现在照料助手的气泡下，爱宠那条没有',
                   'trace 的 runs 里 assistant 运行的 tool_summary 有一条 query_observations（allowed=true）',
                   '消息的 sources 只在 assistant 那条上，爱宠那条是空数组'],
    },
    'search': {
        'title': '联网状态与网页来源：照料助手先显示核实中，再出字并带网页来源卡',
        'trigger': '联网核实一下',
        'target': 'assistant',
        'assistant': ['刚核实过：', '这条结论来自下面的来源页，', '具体细节以来源为准。'],
        'search': True,
        'delay': 0.8,
        'expect': ['出字前这条消息显示「核实中」一类的状态，正文里不出现状态文字',
                   '网页来源卡只出现在照料助手那条；点开是夹具域名 example.com',
                   'trace 的 runs 里 assistant 运行的 tool_summary 有一条 reason=server_side 的 web_search'],
    },
    'pet': {
        'title': '不点名：闲聊由爱宠一个人回',
        'trigger': '想我了吗',
        'target': 'auto',
        'pet': ['想啦！', '一直趴在门口等你。'],
        'delay': 0.5,
        'expect': ['只有爱宠一条气泡，照料助手不出现'],
    },
    'named_pet': {
        'title': '点名爱宠但要求查证：只有爱宠回答，它说不懂并建议问照料助手',
        'trigger': '我今天有点累',
        'target': 'pet',
        'pet': ['辛苦啦，抱抱你。', '走动的事我不懂，你问照料助手吧。'],
        'delay': 0.6,
        'expect': ['只有爱宠一条气泡：没有任何数据或结论，建议去问照料助手',
                   'trace 里 pet 那条运行的 tool_summary 为空（爱宠没有可用的查证工具）',
                   'trace 的 route.source=target：派发由主人的选择决定，不看消息内容'],
    },
    'assistant': {
        'title': '只点照料助手：专业问题一个人答',
        'trigger': '只问照料助手',
        'target': 'assistant',
        'assistant': ['我查了今天的记录：', '还没有走动数据，', '暂时没法告诉你走了多久。'],
        'delay': 0.4,
        'expect': ['只有照料助手一条气泡，爱宠不出现'],
    },
    'scope_out': {
        'title': '范围外：照料助手自报范围外，不挂工具如实拒答',
        'trigger': '帮我写段代码',
        'target': 'auto',
        'handoff': True,
        'scope': 'out_of_scope',
        'pet': ['这个我不会，', '让照料助手看看。'],
        'assistant': ['这个我帮不了。', '我只回答养宠相关的问题。'],
        'delay': 0.5,
        'expect': ['爱宠转交后，照料助手如实说明不在服务范围内',
                   'trace 里 assistant 运行的 tool_summary 为空（自报范围外不挂工具）',
                   'trace 的 route.scope=out_of_scope'],
    },
    'default': {
        'title': '其它消息：爱宠先回（默认由爱宠接话）',
        'trigger': None,
        'target': 'auto',
        # 两位都要有台词：剧本只兜底「未指定」的指向，主人明确选了某一位时剧本不会改指向，
        # 只写一位的台词会让另一位取不到台词、模型返回空正文
        # （ValueError: 模型未返回可用回复），界面上表现为一条失败气泡。
        'pet': ['我在呢。', '今天也在等你回来。'],
        'assistant': ['我在。', '有什么想了解的可以直接问我。'],
        'delay': 0.3,
        'expect': ['只有爱宠一条气泡'],
    },
}

STATE = {'override': None, 'sticky': False, 'turns': {}, 'frames': [], 'counts': {}, 'last': {},
         'originals': None}
LOCK = threading.Lock()
CURRENT = threading.local()
LEGACY_REPLY = '我在。这是一条旧协议的回复。'


# ---------------------------------------------------------------------------
# 剧本选择
# ---------------------------------------------------------------------------

def _pick(text):
    """选剧本：覆盖（常驻或一次性，一次性用完即清）优先，其次触发句，都没命中走 default。调用方持锁。"""
    override = STATE['override']
    if override:
        if not STATE['sticky']:
            STATE['override'] = None
        return override
    for name, scenario in SCENARIOS.items():
        if scenario['trigger'] and scenario['trigger'] in (text or ''):
            return name
    return 'default'


def _script():
    """当前线程正在处理的回合（group.process_turn 设置）对应的剧本。"""
    turn_id = getattr(CURRENT, 'turn_id', None)
    with LOCK:
        name = STATE['turns'].get(turn_id, 'default') if turn_id is not None else 'default'
    return SCENARIOS[name], name


def _attempts(role):
    """这条消息是第几次生成（领取时 attempts 已 +1）：失败剧本只失败第一次。"""
    turn_id = getattr(CURRENT, 'turn_id', None)
    if turn_id is None:
        return 1
    with session_scope() as s:
        return s.execute(select(AgentMessage.attempts).where(
            AgentMessage.turn_id == turn_id, AgentMessage.role == role)).scalar_one_or_none() or 1


def _stream(chunks, on_delta, delay, base='', prefix=''):
    text = base
    if prefix:
        text = prefix
        if on_delta:
            on_delta(text)
    for index, chunk in enumerate(chunks):
        text += chunk
        if on_delta:
            on_delta(text)
        if index < len(chunks) - 1:
            time.sleep(delay)


def _assistant_stance(scenario):
    """照料助手的范围声明行（W18：范围由它自己这一次调用给出，服务端剥离、不上屏）。"""
    scope = 'out_of_scope' if scenario.get('scope') == 'out_of_scope' else 'allowed'
    intent = 'search' if scenario.get('search') else 'read'
    return json.dumps({'scope': scope, 'intent': intent, 'speaker': 'assistant'}, ensure_ascii=False)


def _today_window():
    """今天零点到现在（Asia/Shanghai，ISO 时间）：query_observations 的入参格式。"""
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {'start': start.isoformat(), 'end': now.isoformat(), 'granularity': 'summary'}


def fake_respond(input_items, tools=None, on_delta=None, max_output_tokens=1800, web_search=False):
    """替换 petsense.agent.model.respond：按系统提示区分是哪一位的角色运行、旧协议。"""
    system = input_items[0].get('content') if input_items else ''
    system = system if isinstance(system, str) else ''
    if system.startswith(loop._PET_SYSTEM_PROMPT[:24]):
        role = 'pet'
    elif system.startswith(loop._GROUP_ASSISTANT_HEAD[:24]):
        role = 'assistant'
    else:
        # 旧协议：模型自己用身份标记开头（与真实模型输出一致），流式正文里也带着标记
        legacy = LEGACY_STANCE + '【照料助手】' + LEGACY_REPLY
        _stream([legacy], on_delta, 0)
        return {'text': legacy, 'function_calls': [], 'usage': {}}
    scenario, name = _script()
    chunks = list(scenario.get(role) or [])
    answered = any(item.get('type') == 'function_call_output' for item in input_items)
    # 范围声明只出现在第一次回复里（与真实模型一致）
    prefix = (_assistant_stance(scenario) + '\n') if (role == 'assistant' and not answered) else ''
    if (scenario.get('fail_first') or {}).get(role) and _attempts(role) <= 1:
        _stream(chunks[:1], on_delta, 0, prefix=prefix)  # 先出一段再失败：中途失败不留下半条正文
        time.sleep(0.3)
        raise RuntimeError('fixture: model unavailable')
    if role == 'pet' and scenario.get('handoff') and not answered:
        # 爱宠转交：先说一句陪伴话，再调用转交工具（服务端据此追加照料助手那条）
        _stream(chunks, on_delta, scenario['delay'])
        return {'text': ''.join(chunks), 'usage': {}, 'function_calls': [
            {'name': 'handoff_to_assistant', 'call_id': 'fixture_call_handoff',
             'arguments': json.dumps({'reason': '这题需要查证数据'}, ensure_ascii=False)}]}
    if role == 'assistant' and scenario.get('tool') and not answered:
        # 第一轮：一句开场后调用查询工具；工具结果回来后第二轮才给正文
        _stream(chunks[:1], on_delta, 0, prefix=prefix)
        return {'text': prefix + chunks[0], 'usage': {}, 'function_calls': [
            {'name': scenario['tool'], 'call_id': 'fixture_call_1',
             'arguments': json.dumps(_today_window(), ensure_ascii=False)}]}
    result = {'text': prefix + ''.join(chunks), 'function_calls': [], 'usage': {}}
    if role == 'assistant' and scenario.get('search'):
        # 联网进度提示先于正文出现（随后被正文覆盖），并带回夹具网页来源
        if on_delta:
            on_delta(model.SEARCH_PROGRESS_HINT)
        time.sleep(0.6)
        result.update(web_search_calls=[{'id': 'fixture_ws_1', 'status': 'completed',
                                         'query': 'petsense fixture', 'openPage': ''}],
                      citations=[FIXTURE_WEB])
    base = ''
    if answered:
        base, chunks = chunks[0], chunks[1:]  # 开场那句第一轮已经出过，第二轮从它之后接着出
    _stream(chunks, on_delta, scenario['delay'], base=base)
    return result


# ---------------------------------------------------------------------------
# 取证钩子：记录每一帧，并按剧本注入断线与乱序
# ---------------------------------------------------------------------------

def _log(frame, **extra):
    entry = {'at': round(time.time(), 3), 'type': frame.get('type'), 'turnId': frame.get('turnId'),
             'messageId': frame.get('messageId'), 'role': frame.get('role'), 'version': frame.get('version')}
    message = frame.get('message')
    if isinstance(message, dict):
        entry.update(messageId=message.get('id'), role=message.get('role'),
                     version=message.get('version'), status=message.get('status'))
    if 'text' in frame:
        entry['chars'] = len(frame.get('text') or '')
    if 'status' in frame:
        entry['status'] = frame.get('status')
    if frame.get('type') == 'turn' and isinstance(frame.get('turn'), dict):
        turn = frame['turn']
        entry.update(turnId=turn.get('id'), turnStatus=turn.get('status'),
                     messages=[(item.get('id'), item.get('role'), item.get('status'), item.get('version'))
                               for item in turn.get('messages') or []])
    entry.update(extra)
    with LOCK:
        STATE['frames'].append(entry)


def _scenario_of(turn_id):
    with LOCK:
        return SCENARIOS[STATE['turns'].get(turn_id, 'default')]


def _drop_connections(user_id, turn_id, role):
    """断开该用户的全部连接，对端立刻收到关闭事件（随后由客户端自行重连）。

    simple-websocket 的 close(reason, message) 里 reason 是数字状态码、message 才是给对端的文字。
    只发关闭帧时本库的处理线程仍阻塞在 recv 里等对端回关闭帧，所以发完帧直接关掉传输层。
    """
    for sock, owner in list(ws.clients.items()):
        if owner != user_id:
            continue
        ws.clients.pop(sock, None)
        ws.protocols.pop(sock, None)
        transport = getattr(sock, 'sock', None)
        try:
            sock.close(message='fixture drop')
            if transport is not None:
                transport.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001 对端已经先断开时跳过
            pass
    _log({'type': 'ws_closed', 'turnId': turn_id, 'role': role}, injected='drop')


def install_hooks():
    """进程内替换模型入口，并给推帧函数套一层取证日志。返回还原函数（测试用）。\n\n    包装函数原样透传 W16 新增的可选参数（会话 id、随 turn 帧返回的会话摘要）。\n    """
    originals = (model.respond, loop.model.respond, group.process_turn, group.accept,
                 ws.push_message_delta, ws.push_message, ws.push_message_status, ws.push_delta, ws.push_turn,
                 ws.push_conversation)
    process_turn, accept = group.process_turn, group.accept
    delta, snapshot, status = ws.push_message_delta, ws.push_message, ws.push_message_status
    legacy_delta, turn_frame = ws.push_delta, ws.push_turn
    conversation_frame = ws.push_conversation

    def tagged_process(turn_id, now=None):
        CURRENT.turn_id = turn_id
        try:
            return process_turn(turn_id, now)
        finally:
            CURRENT.turn_id = None

    def tagged_accept(session, turn):
        with LOCK:  # 选剧本与登记回合在同一把锁里完成，覆盖的消耗才是原子的
            name = _pick(turn.text)
            STATE['turns'][turn.id] = name
            # 剧本给这一轮定一个默认指向（W18：派发由主人的选择决定）。
            # 主人/App 明确选过就以主人的为准——剧本只兜底「未指定」。
            if turn.target == 'auto':
                turn.target = SCENARIOS[name].get('target', 'auto')
        return accept(session, turn)

    def pushed_delta(user_id, pet_id, turn_id, message_id, role, text, version, conversation_id=None):
        scenario = _scenario_of(turn_id)
        previous = STATE['last'].get(message_id)
        if scenario.get('inject') and previous:
            delta(user_id, pet_id, turn_id, message_id, role, previous[0], previous[1], conversation_id)
            _log({'type': 'message_delta', 'turnId': turn_id, 'messageId': message_id, 'role': role,
                  'text': previous[0], 'version': previous[1]}, injected='stale')
        delta(user_id, pet_id, turn_id, message_id, role, text, version, conversation_id)
        _log({'type': 'message_delta', 'turnId': turn_id, 'messageId': message_id, 'role': role,
              'text': text, 'version': version})
        if scenario.get('inject') and previous:
            delta(user_id, pet_id, turn_id, message_id, role, previous[0], previous[1], conversation_id)
            _log({'type': 'message_delta', 'turnId': turn_id, 'messageId': message_id, 'role': role,
                  'text': previous[0], 'version': previous[1]}, injected='duplicate')
        with LOCK:
            STATE['last'][message_id] = (text, version)
            STATE['counts'][message_id] = STATE['counts'].get(message_id, 0) + 1
            count = STATE['counts'][message_id]
        drop = scenario.get('drop_after')
        if drop and role == drop['role'] and count == drop['deltas']:
            _drop_connections(user_id, turn_id, role)

    def pushed_snapshot(user_id, pet_id, message, conversation_id=None):
        snapshot(user_id, pet_id, message, conversation_id)
        _log({'type': 'message', 'turnId': message.get('turnId'), 'message': message})

    def pushed_status(user_id, pet_id, turn_id, message_id, role, state, conversation_id=None):
        status(user_id, pet_id, turn_id, message_id, role, state, conversation_id)
        _log({'type': 'message_status', 'turnId': turn_id, 'messageId': message_id, 'role': role,
              'status': state})

    def pushed_legacy(user_id, pet_id, turn_id, text, speaker, conversation_id=None):
        legacy_delta(user_id, pet_id, turn_id, text, speaker, conversation_id)
        _log({'type': 'delta', 'turnId': turn_id, 'role': speaker, 'text': text})

    def pushed_turn(user_id, pet_id, turn_public, conversation=None):
        turn_frame(user_id, pet_id, turn_public, conversation)
        _log({'type': 'turn', 'turnId': (turn_public or {}).get('id'), 'turn': turn_public})

    def pushed_conversation(user_id, conversation):
        conversation_frame(user_id, conversation)
        _log({'type': 'conversation', 'conversation': conversation})

    for module in (model, loop.model):
        module.respond = fake_respond
    group.process_turn, group.accept = tagged_process, tagged_accept
    ws.push_message_delta, ws.push_message, ws.push_message_status = pushed_delta, pushed_snapshot, pushed_status
    ws.push_delta, ws.push_turn, ws.push_conversation = pushed_legacy, pushed_turn, pushed_conversation

    def restore():
        (model.respond, loop.model.respond, group.process_turn, group.accept,
         ws.push_message_delta, ws.push_message, ws.push_message_status, ws.push_delta, ws.push_turn,
         ws.push_conversation) = originals
        with LOCK:
            STATE.update(override=None, sticky=False, turns={}, frames=[], counts={}, last={})
    return restore


# ---------------------------------------------------------------------------
# 应用与控制接口
# ---------------------------------------------------------------------------

def _seed(app):
    """预置验收账号与宠物（走正式注册接口；账号已存在则跳过）。"""
    client = app.test_client()
    registered = client.post('/api/app/auth/register', json={
        'username': SEED_USER, 'password': SEED_PASSWORD, 'displayName': '群聊验收'}).json
    if not registered.get('success'):
        return
    client.post('/api/app/pets', json={'name': SEED_PET, 'species': 'dog'},
                headers={'X-Petsense-Session': registered['token']})


def _run_views(turn_id):
    with session_scope() as s:
        rows = s.execute(select(AgentRun).where(AgentRun.turn_id == turn_id).order_by(AgentRun.id)).scalars().all()
        return [runs.summarize(row) for row in rows]


def trace(turn_id):
    """回合取证：路由记录 + 消息 + 运行轨迹 + 逐帧日志（读已提交的数据）。"""
    with session_scope() as s:
        turn = s.get(AgentTurn, turn_id)
        if turn is None:
            return None
        view = group.route_view(s, turn_id)
        payload = group.turn_public(s, turn)
    with LOCK:
        name = STATE['turns'].get(turn_id)
        frames = [dict(entry) for entry in STATE['frames'] if entry.get('turnId') == turn_id]
    return {'turnId': turn_id, 'scenario': name, 'scenarioTitle': SCENARIOS[name]['title'] if name else None,
            'turn': payload, 'route': view['route'], 'messages': view['messages'],
            'runs': _run_views(turn_id), 'frames': frames}


def latest_turn_id():
    with session_scope() as s:
        return s.execute(select(func.max(AgentTurn.id))).scalar()


def register_routes(app):
    @app.get('/__test__/health')
    def health():
        with LOCK:
            override = {'name': STATE['override'], 'sticky': STATE['sticky']}
        return {'ok': True, 'root': str(config.RUNTIME_ROOT), 'protocol': group.PROTOCOL,
                'enabled': group.enabled(), 'scheduler': group.jobs.scheduler_enabled(),
                'wsClients': len(ws.clients), 'turns': len(STATE['turns']), 'override': override,
                'seed': {'username': SEED_USER, 'password': SEED_PASSWORD, 'pet': SEED_PET}}

    @app.get('/__test__/scenarios')
    def scenarios():
        return {'scenarios': [{'name': name, 'title': item['title'], 'trigger': item['trigger'],
                               'expect': item['expect']} for name, item in SCENARIOS.items()]}

    @app.post('/__test__/scenario')
    def scenario():
        data = request.get_json(silent=True) or {}
        name = data.get('name')
        if name is not None and name not in SCENARIOS:
            return {'ok': False, 'message': 'unknown scenario', 'scenarios': sorted(SCENARIOS)}, 400
        with LOCK:
            STATE['override'], STATE['sticky'] = name, bool(data.get('sticky'))
        return {'ok': True, 'override': name, 'sticky': STATE['sticky']}

    @app.post('/__test__/reset')
    def reset():
        with LOCK:
            STATE.update(override=None, sticky=False, turns={}, frames=[], counts={}, last={})
        return {'ok': True}

    @app.get('/__test__/trace')
    def trace_route():
        turn_id = latest_turn_id() if request.args.get('latest') else request.args.get('turn', type=int)
        if turn_id is None:
            return {'ok': False, 'message': 'turn=<回合 id> 或 latest=1'}, 400
        result = trace(turn_id)
        return result if result is not None else ({'ok': False, 'message': 'turn not found'}, 404)


def build_app(start_background=True):
    """创建夹具应用：正式 app + 控制接口。调用方负责 install_hooks()；start_background=False 供测试。

    控制接口必须先注册：预置账号会走一次正式请求，之后 Flask 不再允许加路由。
    """
    from petsense.app import create_app
    app = create_app(start_background=start_background)
    app.json.ensure_ascii = False  # curl 取证时中文直接可读
    register_routes(app)
    if os.environ.get('PETSENSE_FIXTURE_SEED', '1') != '0':
        _seed(app)
    return app


if __name__ == '__main__':
    install_hooks()
    application = build_app()
    port = int(os.environ.get('PETSENSE_FIXTURE_PORT', '8094'))
    print(f'[group-fixture] root={config.RUNTIME_ROOT} port={port} protocol={group.PROTOCOL} '
          f'account={SEED_USER}/{SEED_PASSWORD} pet={SEED_PET}', flush=True)
    application.run(host='127.0.0.1', port=port, threaded=True, debug=False, use_reloader=False)
