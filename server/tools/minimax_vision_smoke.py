"""MiniMax Responses API 图片输入冒烟（W07 spec 前置外部验证）。

P00 §7 的图片证据（256×256 JPEG 识别正确）来自旧链路；W01 后主对话
整体在 /v1/responses，图片能否进入该端点是 W07 的硬前提——不支持即为
方案级风险，按工作包规则停下来向负责人澄清，不自行换方案。

实测项：
  V1  input_image + data URL（base64）直传
  V1b input_image + {url: ...} 对象形态（OpenAI Chat Completions 形状）
  V2  图片与 Function Calling 工具共存（agent 循环恒带 tools）
  V3  多轮历史中的图片保留（agent 全量回显会话的形态）
  V4  usage 与错误形态记录

只读：不触数据库、不写文件（测试图内存生成，内容可断言：
白底、黑色大字 PETSENSE 7319、红色圆形、蓝色三角形）。

用法：
  本地：MINIMAX_API_KEY=... python3 minimax_vision_smoke.py
  云端：MINIMAX_API_KEY_FILE=... .venv/bin/python - < minimax_vision_smoke.py

退出码只反映脚本是否跑完；各项结论是输出数据。
"""
import base64
import io
import json
import os
import sys

import requests

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # 云端 release venv 与本地均应具备；缺失时明确报告
    Image = None

TIMEOUT = (10, 120)
EVIDENCE_LIMIT = 300

QUESTION = '请简短描述这张图：图中的文字是什么？有哪些形状和颜色？'


def load_config():
    key = os.environ.get('MINIMAX_API_KEY')
    if not key and os.environ.get('MINIMAX_API_KEY_FILE'):
        key = open(os.environ['MINIMAX_API_KEY_FILE']).read().strip()
    if not key:
        raise SystemExit('未配置 MiniMax 凭据（MINIMAX_API_KEY 或 MINIMAX_API_KEY_FILE）')
    return key, os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1').rstrip('/'), \
        os.environ.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash')


def make_test_jpeg():
    """1024×1024 白底：黑色大字 PETSENSE 7319、红色圆形、蓝色三角形。"""
    if Image is None:
        raise SystemExit('Pillow 不可用，无法生成测试图')
    img = Image.new('RGB', (1024, 1024), 'white')
    draw = ImageDraw.Draw(img)
    draw.ellipse([110, 420, 430, 740], fill=(220, 30, 30))
    draw.polygon([(600, 740), (900, 740), (750, 440)], fill=(30, 60, 220))
    try:
        font = ImageFont.load_default(size=80)
    except TypeError:  # 旧 Pillow 无 sized default font
        font = None
    if font:
        draw.text((140, 120), 'PETSENSE 7319', fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return buf.getvalue(), font is not None


def post(key, base, payload):
    try:
        response = requests.post(base + '/responses', headers={'Authorization': 'Bearer ' + key},
                                 json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, f'{type(exc).__name__}: {exc}'
    try:
        return response.status_code, response.json() if response.text else ''
    except ValueError:
        return response.status_code, response.text


def evidence(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:EVIDENCE_LIMIT].replace('\n', ' ')


def output_text(body):
    if not isinstance(body, dict):
        return ''
    if isinstance(body.get('output_text'), str) and body['output_text']:
        return body['output_text']
    parts = []

    def take(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') in ('output_text', 'text') and block.get('text'):
                    parts.append(block['text'])
    for item in body.get('output') or []:
        if isinstance(item, dict) and item.get('type') in ('message', None):
            take(item.get('content'))
    return ''.join(parts)


def describe_ok(text, has_text):
    """断言：形状颜色必查；文字仅在有字模时必查（Pillow 版本兜底）。"""
    if not text.strip():
        return False, '空回复'
    missing = []
    if '红' not in text or ('圆' not in text):
        missing.append('红圆')
    if '蓝' not in text or '三角' not in text:
        missing.append('蓝三角')
    if has_text and '7319' not in text:
        missing.append('文字7319')
    return (not missing), ('缺 ' + '/'.join(missing) if missing else '')


def main():
    key, base, model = load_config()
    print(f'# MiniMax Responses 图片输入冒烟 base={base} model={model}')
    jpeg, has_text = make_test_jpeg()
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()
    print(f'测试图: {len(jpeg)} bytes JPEG（字模={"有" if has_text else "无，文字断言降级"}）')

    # V1 data URL 字符串形态
    s1, b1 = post(key, base, {'model': model, 'input': [{'role': 'user', 'content': [
        {'type': 'input_text', 'text': QUESTION},
        {'type': 'input_image', 'image_url': data_url}]}]})
    t1 = output_text(b1) if s1 == 200 else ''
    ok1, why1 = describe_ok(t1, has_text) if s1 == 200 else (False, f'http-{s1}')
    print(f'[V1] data URL 字符串 -> {"pass" if ok1 else "FAIL"} ({why1}): {evidence(t1 or b1)[:200]}')
    if isinstance(b1, dict) and b1.get('usage'):
        print(f'     usage: {evidence(b1["usage"])}')

    # V1b {url: ...} 对象形态（仅当 V1 失败时探测形状兼容性）
    ok1b, t1b, s1b = False, '', None
    if not ok1:
        s1b, b1b = post(key, base, {'model': model, 'input': [{'role': 'user', 'content': [
            {'type': 'input_image', 'image_url': {'url': data_url}},
            {'type': 'input_text', 'text': QUESTION}]}]})
        t1b = output_text(b1b) if s1b == 200 else ''
        ok1b, _ = describe_ok(t1b, has_text) if s1b == 200 else (False, f'http-{s1b}')
        print(f'[V1b] {{url}} 对象形态 -> {"pass" if ok1b else "FAIL"} (HTTP {s1b}): {evidence(t1b or b1b)[:200]}')

    image_supported = ok1 or ok1b
    block_form = 'string' if ok1 else ('object' if ok1b else 'none')

    # V2 图片 + Function Calling 共存
    tool = {'type': 'function', 'name': 'query_observations',
            'description': '查询宠物项圈在指定时间窗口的真实观测数据',
            'parameters': {'type': 'object', 'properties': {
                'start': {'type': 'string'}, 'end': {'type': 'string'}},
                'required': ['start', 'end'], 'additionalProperties': False}}
    image_block = ({'type': 'input_image', 'image_url': data_url} if ok1
                   else {'type': 'input_image', 'image_url': {'url': data_url}})
    s2, b2 = post(key, base, {'model': model, 'tools': [tool], 'input': [
        {'role': 'system', 'content': '你是宠物助手。看图回答；除非主人问及观测数据，否则不要调用工具。'},
        {'role': 'user', 'content': [{'type': 'input_text', 'text': QUESTION}, image_block]}]})
    t2 = output_text(b2) if s2 == 200 else ''
    ok2, why2 = describe_ok(t2, has_text) if s2 == 200 else (False, f'http-{s2}')
    print(f'[V2] 图片+FC 共存 -> {"pass" if ok2 else "FAIL"} ({why2}): {evidence(t2 or b2)[:200]}')

    # V3 多轮历史保留图片（全量回显形态：首轮图、次轮追问）
    ok3, t3, s3 = False, '', None
    if image_supported:
        s3, b3 = post(key, base, {'model': model, 'input': [
            {'role': 'user', 'content': [{'type': 'input_text', 'text': '看一下这张图。'}, image_block]},
            {'role': 'assistant', 'content': '好的，我看到了。'},
            {'role': 'user', 'content': [{'type': 'input_text', 'text': '图中文字是什么？只回答文字内容。'}]}]})
        t3 = output_text(b3) if s3 == 200 else ''
        ok3 = s3 == 200 and (('7319' in t3) or (not has_text))
        print(f'[V3] 历史图片追问 -> {"pass" if ok3 else "FAIL"} (HTTP {s3}): {evidence(t3 or b3)[:200]}')
        if isinstance(b3, dict) and b3.get('usage'):
            print(f'     usage: {evidence(b3["usage"])}')

    print('\n== 结论矩阵（回填 agent-w07-spec.md 外部验证）==')
    v1b_state = 'available' if ok1b else ('untested(V1已过，跳过)' if ok1 else 'unavailable')
    print(f'V1 data URL: {"available" if ok1 else "unavailable"}; V1b 对象形态: {v1b_state}'
          f'（采用 {block_form}）')
    print(f'V2 图片+FC 共存: {"available" if ok2 else "unavailable"}')
    print(f'V3 历史图片保留: {"available" if ok3 else ("untested" if not image_supported else "unavailable")}')
    verdict = '可行' if (image_supported and ok2) else '存在阻断项，按工作包规则停下来向负责人澄清'
    print(f'W07 图片输入判定: {verdict}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
