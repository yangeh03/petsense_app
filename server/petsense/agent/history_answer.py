"""Render common observation questions from the query result."""
import re

from petsense.agent import facts

_MATCH_VITALS = {
    'heartRate': r'心率|心跳',
    'spo2': r'血氧',
    'bodyTemp': r'体温|传感器温度',
    'envTemp': r'环境温度|室温',
}
_SOUND = re.compile(r'声音|呜咽|吠叫|嚎叫|事件明细|明细')
_WALK = re.compile(r'走动|活动|步行')
_REGION = re.compile(r'房间|区域|在哪|哪里')


def answer(question, records, speaker, previous_question=''):
    """Return a factual answer for recognized questions, or None for free-form summaries."""
    sound = bool(_SOUND.search(question) or (
        _SOUND.search(previous_question) and re.search(r'刚才|那批|占总数|多少百分比', question)
    ))
    sound_rate = sound and bool(re.search(r'百分|占比|占总数|比例', question))
    walk = bool(_WALK.search(question))
    coverage = '覆盖' in question or '观测了多久' in question or '观测时长' in question
    region = bool(_REGION.search(question))
    vitals = [key for key, pattern in _MATCH_VITALS.items() if re.search(pattern, question)]
    if not any((sound, walk, coverage, region, vitals)):
        return None
    lines = []
    for record in records:
        if record['state'] == 'invalid_window':
            lines.append('这个时间范围不明确，请告诉我想看哪一天或哪个时段。')
            continue
        title = facts.window_title(record)
        if record['state'] == 'query_failed':
            lines.append(f'{title}的记录暂时查询失败。')
            continue
        data = record['result']
        if record['state'] == 'no_coverage':
            lines.append(f'{title}没有项圈观测记录，无法判断当时的活动或指标。')
            continue
        items = []
        if coverage or walk:
            items.append(f'总观测覆盖 {data["observedSeconds"]:.1f} 秒')
        if walk:
            if data['actionObservedSeconds'] > 0:
                items.append(f'记录到走动 {data["actionSeconds"].get("walking", 0):.1f} 秒')
            else:
                items.append('这段时间没有有效的动作判定，无法判断走动时长')
        if sound:
            total = data['soundEventCount']
            returned = data['soundEventsReturned']
            items.append(f'声音事件总计 {total} 条')
            if data['soundEventsTruncated'] or sound_rate:
                items.append(f'返回 {returned} 条明细')
                if total and sound_rate:
                    items.append(f'明细约占总数 {returned / total:.1%}')
            if '覆盖' in question:
                if data['soundObservedSeconds'] or not total:
                    items.append(f'声音覆盖记录为 {data["soundObservedSeconds"]:.1f} 秒')
                else:
                    items.append('此窗口没有可用的声音覆盖时长记录')
            if total and not sound_rate and not data['soundEventsTruncated']:
                for group in data['soundBreakdown']:
                    label = facts.SOUND_LABELS.get(group['label'], group['label'])
                    piece = f'{label} {group["count"]} 条'
                    if group['experimental']:
                        piece += f'（其中 {group["experimental"]} 条为实验性标签）'
                    items.append(piece)
        if region:
            items.append('这段记录没有区域信息，无法判断它在哪个房间')
        for key in vitals:
            title_cn, unit = facts.METRIC_LABELS[key]
            metric = data['metrics'].get(key)
            if metric and metric['count']:
                items.append(f'{title_cn}平均 {metric["avg"]}{unit}')
            else:
                items.append(f'{title_cn}无有效读数')
        lines.append(f'{title}：' + '，'.join(items) + '。')
    if walk and any(record['state'] == 'ok' for record in records):
        lines.append('这些数字只涵盖已观测时段，未覆盖时段的活动量未知。')
    return facts.normalize_reply('\n'.join(lines), speaker)


def simple_day_specs(question):
    """Recognize explicit whole-day requests without guessing sub-day boundaries."""
    if re.search(r'\d{1,2}[:：]\d{2}|(?:上午|下午|早上|傍晚|凌晨|昨晚|今早)|\d{1,2}\s*[点时]|(?:明天|最近|过去|上周|本周)', question):
        return []
    if re.search(r'从[^，。]{0,24}(?:到|至)|(?:[日号]|\d{4}-\d{1,2}-\d{1,2})\s*[—–~～至到]', question) and '次日零点' not in question:
        return []
    dates = re.findall(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]|(\d{4})-(\d{1,2})-(\d{1,2})', question)
    specs = [f'{int(y or yy):04d}-{int(m or mm):02d}-{int(d or dd):02d}'
             for y, m, d, yy, mm, dd in dates]
    # Models handle expressions with an omitted second year or month.
    if specs and len(re.findall(r'\d{1,2}\s*[日号]|\d{4}-\d{1,2}-\d{1,2}', question)) != len(specs):
        return []
    relative_days = [day for day in ('昨天', '今天', '前天') if day in question]
    if (specs and relative_days) or len(relative_days) > 1:
        return []
    return list(dict.fromkeys(specs or relative_days))
