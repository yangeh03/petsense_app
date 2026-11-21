"""结构化事实的模型视图与模板回复。

本轮历史数字只能来自本轮真实成功的 owner scoped 查询；这里把查询结果
翻译成无设备编码、无原始字段名的中文事实，并为常见情形（无覆盖、查询
失败、需澄清、模型失败降级）提供确定性模板，正文身份与持久 speaker 一致。"""
import re
from datetime import datetime, timedelta

from petsense.agent.routing import SHANGHAI

MARKERS = {'pet': '【爱宠】', 'assistant': '【照料助手】'}
LEADING_MARKERS = re.compile(r'^\s*(?:【[^】]{1,16}】\s*)+')

ACTION_LABELS = {'walking': '走动', 'standing': '站立', 'sitting': '坐着', 'lying down': '躺卧'}
SOUND_LABELS = {'bark': '吠叫', 'howl': '嚎叫', 'whine': '呜咽'}
METRIC_LABELS = {'heartRate': ('心率', '次/分'), 'spo2': ('血氧', '%'),
                 'bodyTemp': ('传感器温度', '℃'), 'envTemp': ('环境温度', '℃')}

NO_COVERAGE_NOTE = '该窗口没有任何分钟记录或声音事件，不能据此推断当天状态，也不得把零当作任何计量的读数。'
FAILED_NOTE = '本轮查询没有成功，不能当作没有数据，也不得使用任何数字。'
TOOL_GUIDANCE = ('以上事实是回复中唯一可信的数据；未列出的数字不得使用，历史消息里的数字不是本轮证据。'
                 '「窗口内没有记录」要如实说明；「查询失败」表示暂时查不了，建议稍后再试；'
                 '「时间无效」时请向主人澄清想看哪个时段，不要编造日期。'
                 '每个已成功查询的窗口，来源链接已由系统附加，主人可在记录页直接点开对应时段。')

SPEAKER_RULE = '以{marker}开头，全文以{role}的身份说话，不得自称另一方的身份或复述内部字段、设备编码。'


def window_title(record):
    label = record.get('label')
    if not record.get('start') or not record.get('end'):
        return label or '该窗口'
    start, end = record.get('start'), record.get('end')
    if start is None or end is None:
        return label or '未知窗口'
    start = start.astimezone(SHANGHAI)
    end = (end - timedelta(microseconds=1)).astimezone(SHANGHAI)
    if start.date() == end.date():
        span = f'{start.month}月{start.day}日'
    else:
        span = f'{start.month}月{start.day}日至{end.month}月{end.day}日'
    return f'{label}（{span}）' if label else span


def normalize_reply(text, speaker):
    """剥掉模型自带的身份标记，换成与持久 speaker 一致的标记。"""
    body = LEADING_MARKERS.sub('', text or '').strip()
    return MARKERS.get(speaker, MARKERS['assistant']) + body


def model_view(record):
    """单窗口事实的模型视图：绝不包含设备编码或原始字段名。"""
    title = window_title(record)
    state = record['state']
    if state == 'invalid_window':
        return {'窗口': title, '状态': '时间无效', '说明': record.get('reason') or '窗口校验未通过'}
    if state == 'query_failed':
        return {'窗口': title, '状态': '查询失败', '说明': FAILED_NOTE}
    result = record.get('result')
    if result is None:
        return {'窗口': title, '状态': '查询失败', '说明': FAILED_NOTE}
    if state == 'no_coverage':
        return {'窗口': title, '状态': '窗口内没有记录', '说明': NO_COVERAGE_NOTE}
    view = {'窗口': title, '状态': '已查到记录',
            '覆盖秒数': round(result['observedSeconds'], 1),
            '分钟记录数': result['minuteCount']}
    actions = {ACTION_LABELS.get(key, key): round(value, 1)
               for key, value in (result.get('actionSeconds') or {}).items() if value}
    if actions:
        view['姿态秒数'] = actions
    total, returned = result['soundEventCount'], result.get('soundEventsReturned', len(result.get('soundEvents') or []))
    view['声音事件总数'] = total
    view['声音覆盖秒数'] = round(result.get('soundObservedSeconds') or 0, 1)
    if total:
        view['声音明细条数'] = returned
        breakdown = [{**entry, 'label': SOUND_LABELS.get(entry['label'], entry['label'])}
                     for entry in result.get('soundBreakdown') or []]
        view['声音类型全量统计'] = [{'类型': entry['label'], '数量': entry['count'],
                                    '其中实验性标注': entry.get('experimental', 0)} for entry in breakdown]
        if result.get('soundEventsTruncated'):
            view['声音明细说明'] = f'明细仅返回前{returned}条（约占总数{round(returned / total * 100, 1)}%），总量以上方总数和全量统计为准。'
    metrics = []
    for key, (name, unit) in METRIC_LABELS.items():
        entry = (result.get('metrics') or {}).get(key) or {}
        if entry.get('count'):
            metrics.append({'指标': name, '读数': f'均值{entry["avg"]}{unit}，范围{entry["min"]}–{entry["max"]}{unit}，{entry["count"]}次有效读数'})
        else:
            metrics.append({'指标': name, '读数': '该窗口无采样'})
    view['体征'] = metrics
    latest = result.get('latest')
    if latest:
        snapshot = {name: f'{value}{unit}' for key, (name, unit) in METRIC_LABELS.items()
                    for value in [(latest.get('health') or {}).get(key)] if value is not None}
        moment = datetime.fromisoformat(latest['receivedAt'].replace('Z', '+00:00')).astimezone(SHANGHAI)
        view['最新一帧'] = {'时间': f'{moment.month}月{moment.day}日{moment.hour}:{moment.minute:02d}（上海时间）',
                           '有效体征': snapshot or '无有效体征'}
    versions = result.get('sourceVersions') or []
    view['识别版本标注'] = '有标注' if versions else '未知'
    learning = result.get('learning') or {}
    if learning:
        view['学习进度'] = (f'动作维度有效天数 {learning.get("validDays", 0)}/{learning.get("requiredDays", 7)}，'
                           '学习仍在进行，不能据此下个体基线结论。')
    view['区域'] = '该窗口没有区域语义，不能判断房间位置'
    return view


def template_summary(records, speaker='assistant'):
    """模型失败时用已取得的结构化事实稳健降级；无事实时不编造。"""
    lines = [MARKERS.get(speaker, MARKERS['assistant'])]
    usable = False
    for record in records:
        title = window_title(record)
        state = record['state']
        if state == 'ok':
            usable = True
            result = record['result']
            piece = f'{title}：覆盖约{result["observedSeconds"]:.1f}秒'
            walking = round((result.get('actionSeconds') or {}).get('walking') or 0, 1)
            if walking:
                piece += f'，走动约{walking}秒'
            if result.get('soundEventCount'):
                piece += f'，声音事件{result["soundEventCount"]}条'
            lines.append(piece + '。')
        elif state == 'no_coverage':
            usable = True
            lines.append(f'{title}：没有记录，无法判断当时的状态。')
        elif state == 'query_failed':
            usable = True
            lines.append(f'{title}：这次没查成，稍后再试一次。')
    if not usable:
        return ''
    if any(record['state'] in ('ok', 'no_coverage') for record in records):
        lines.append('对应时段的记录来源已附上，可点开查看。')
    return ''.join(lines)


def clarify_reply(speaker='assistant'):
    return MARKERS.get(speaker, MARKERS['assistant']) + '你想看哪个时段的记录？可以说昨天、今天或具体日期，我帮你查。'
