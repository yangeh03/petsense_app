"""Expo Push Service 客户端（W13，iOS 首版）：投递票据与回执。

与 Expo Push Service 的两个接口：
- 投递：``POST https://exp.host/--/api/v2/push/send`` → 返回 ticket（id 或错误码）；
- 回执：``POST https://exp.host/--/api/v2/push/getReceipts`` → 返回送达/失败回执。

设计要点：
- **传输层可替换**：``set_transport`` 让单测用本地假服务覆盖全部结果分支，不接触
  真实服务方。生产用默认的 urllib 传输。
- **结果不明与明确失败分开**：网络异常（超时、连接中断）抛 ``OutcomeUnknown``——
  请求可能已经到达服务方，是否接收未知；调用方必须按「未知」处理而不是重发。
  HTTP 4xx/5xx 与 JSON 里的 ``status='error'`` 是明确失败，可按错误码分类。
- **不打印 token**：本模块不写日志，也不把 token 放进异常消息。
"""
import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

SEND_URL = 'https://exp.host/--/api/v2/push/send'
RECEIPTS_URL = 'https://exp.host/--/api/v2/push/getReceipts'
DEFAULT_TIMEOUT_S = float(os.environ.get('PETSENSE_PUSH_TIMEOUT_S', '10'))
ACCESS_TOKEN_ENV = 'PETSENSE_EXPO_ACCESS_TOKEN'
MAX_PAYLOAD_BYTES = 4096  # Expo 单条消息上限

#: 任何对外可见的文本都要过一遍：token 形态的东西一律替换掉。
_TOKEN_PATTERN = re.compile(r'(ExponentPushToken|ExpoPushToken)\[[^\]]*\]')


def scrub(text):
    """去掉文本里的 token 形态片段（错误信息与审计行都走这里）。"""
    if text is None:
        return None
    return _TOKEN_PATTERN.sub('[token]', str(text))

# Expo 错误码 → 本包的处理口径
ERROR_DEVICE_NOT_REGISTERED = 'DeviceNotRegistered'   # token 失效：永久剔除安装实例
ERROR_MESSAGE_TOO_BIG = 'MessageTooBig'               # 载荷过大：明确失败，不重试
ERROR_MESSAGE_RATE_TOO_HIGH = 'MessageRateTooHigh'   # 服务方限流：明确可恢复，可有限重试
ERROR_RECEIPT_NOT_AVAILABLE = 'ReceiptNotAvailable'   # 回执尚未就绪：稍后再查
RECOVERABLE_ERRORS = (ERROR_MESSAGE_RATE_TOO_HIGH,)


class OutcomeUnknown(Exception):
    """服务方是否接收未知（超时、连接中断等）。调用方须停止重试以免重复投递。"""


class ProviderError(Exception):
    """明确失败：服务方返回了错误响应或错误票据。"""

    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


class Transport:
    """传输层接口：post(url, payload) -> dict。"""

    def post(self, url, payload):  # pragma: no cover - 接口定义
        raise NotImplementedError


class UrllibTransport(Transport):
    """默认传输：标准库 urllib（与仓库其它外部调用一致，不引入新依赖）。"""

    def __init__(self, timeout_s=DEFAULT_TIMEOUT_S):
        self.timeout_s = timeout_s

    def post(self, url, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        access_token = os.environ.get(ACCESS_TOKEN_ENV, '').strip()
        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
        request = urllib.request.Request(url, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:  # 明确的服务方错误
            raise ProviderError('服务方返回 HTTP %d' % exc.code, code='http_%d' % exc.code,
                                status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # 连接层失败：请求可能已到达服务方，结果未知
            raise OutcomeUnknown('投递请求结果未知') from exc
        try:
            return json.loads(raw) if raw else {}
        except ValueError as exc:
            raise OutcomeUnknown('服务方响应不可解析') from exc


_transport = UrllibTransport()


def set_transport(transport):
    """替换传输层（单测注入假服务）。传 None 恢复默认。"""
    global _transport
    _transport = transport or UrllibTransport()


def current_transport():
    return _transport


def _normalize_ticket(entry):
    """把单条票据结果归一成 {'status','id','code','message'}。

    ``message`` 会被清洗（服务方有时把 token 原样带在错误描述里）。
    """
    if not isinstance(entry, dict):
        return {'status': 'error', 'id': None, 'code': 'MalformedResponse',
                'message': '服务方返回了无法识别的票据'}
    if entry.get('status') == 'ok':
        return {'status': 'ok', 'id': entry.get('id'), 'code': None, 'message': None}
    details = entry.get('details') or {}
    error = details.get('error') if isinstance(details, dict) else None
    message = details.get('message') if isinstance(details, dict) else None
    return {'status': 'error', 'id': None, 'code': error or 'UnknownError',
            'message': scrub(message)}


def send_messages(messages):
    """提交推送消息。返回与 messages 等长的票据结果列表。

    messages 为 Expo 载荷列表（不含 to 之外的内部字段）。任何一条出错不影响
    其他条：Expo 按条返回结果。

    异常：``OutcomeUnknown``（结果未知，停止重试）/ ``ProviderError``（明确失败）。
    """
    if not messages:
        return []
    payload = {'messages': messages}
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_PAYLOAD_BYTES:
        # 载荷过大是明确的 schema 错误：不重试，也不把内容写进错误信息
        raise ProviderError('推送载荷超出长度上限', code='payload_too_large')
    response = _transport.post(SEND_URL, payload)
    data = response.get('data') if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise ProviderError('服务方响应缺少票据列表', code='MalformedResponse')
    tickets = []
    for entry in data:
        ticket = _normalize_ticket(entry)
        if ticket['status'] == 'ok' and not ticket['id']:
            ticket = {'status': 'error', 'id': None, 'code': 'MalformedResponse',
                      'message': '票据缺少 id'}
        tickets.append(ticket)
    return tickets


def get_receipts(ticket_ids):
    """查询回执。返回 {ticket_id: receipt} 字典。

    回执尚未就绪的服务方不返回该条目，调用方按「仍待回执」处理。
    """
    ids = [tid for tid in ticket_ids if tid]
    if not ids:
        return {}
    response = _transport.post(RECEIPTS_URL, {'ids': ids})
    data = response.get('data') if isinstance(response, dict) else None
    receipts = {}
    if not isinstance(data, dict):
        return receipts
    for ticket_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        receipts[ticket_id] = {
            'status': entry.get('status'),
            'id': entry.get('id'),
            'message': entry.get('message'),
            'details': entry.get('details') or {},
        }
    return receipts
