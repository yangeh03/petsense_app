"""Deterministic routing guards and Asia/Shanghai window resolution for history questions.

模型输出的日期只作为计划；这里的解析与校验是唯一入口，保证窗口半开、
相对日按上海自然日展开、且不超过观测保留期。"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
MAX_WINDOW_DAYS = 90

_METRIC = re.compile(r'走动|活动量|移动|姿态|站|坐|躺|睡|睡眠|声音|叫|吠|哼|呜咽|嚎|噪声|吵闹|体温|温度|心率|心跳|血氧|呼吸|环境|室温|区域|房间|覆盖|样本|事件|记录|数据|观测|体征|指标|统计|趋势|几次|多少次')
_TIME = re.compile(r'昨天|今天|前天|上午|下午|晚上|晚上好|凌晨|中午|傍晚|早上|昨晚|今早|周[一二三四五六日天末]|礼拜|上月|本月|这周|上周|下周|最近|过去|此前|刚才|之前|那时候|多久|时长|占比|百分比|百分之|比例|对比|比较|还是|变化|平均|峰值|最高|最低|\d+\s*[日号月分分钟小时天周]|11\s*月|\d{1,2}[-/.]\d{1,2}')

_TOKENS = {
    '现在': 0, '今天': 0, '明天': 1, '昨天': -1, '前天': -2, '大前天': -3,
}
_SPAN = re.compile(r'(?:最近|近|过去)\s*(\d{1,3})\s*天')


def looks_like_history(text):
    """数据问答的确定性兜底：指标词与时点词同时出现，即使模型误判也不放走。"""
    if not isinstance(text, str):
        return False
    if re.search(r'不聊数据|别聊数据|留意|持续监测|主动.{0,6}回信', text):
        return False
    if re.search(r'联网搜索|上网搜|网页搜索', text) and not re.search(r'它|本宠|我家|项圈记录', text):
        return False
    return bool(_METRIC.search(text)) and bool(_TIME.search(text))


def _day_start(day):
    return datetime(day.year, day.month, day.day, tzinfo=SHANGHAI)


def _parse_boundary(value, now, position):
    """把模型给的单侧边界解析为带时区时刻；自然日词一律落在上海零点。"""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if not isinstance(value, str):
        raise ValueError('时间格式无效')
    text = value.strip()
    if not text:
        raise ValueError('时间为空')
    span = _SPAN.fullmatch(text)
    if span:
        days = int(span.group(1))
        if not 1 <= days <= MAX_WINDOW_DAYS:
            raise ValueError('天数应在1到90之间')
        if position == 'end':
            return _day_start(now.date()) + timedelta(days=1)
        return _day_start(now.date()) - timedelta(days=days - 1)
    token = _TOKENS.get(text)
    if token is not None:
        return _day_start(now.date()) + timedelta(days=token)
    if text in ('本周', '这周'):
        return _day_start((now - timedelta(days=now.weekday())).date())
    if text == '上周':
        return _day_start((now - timedelta(days=now.weekday() + 7)).date())
    if text == '下周':
        return _day_start((now + timedelta(days=7 - now.weekday())).date())
    if text == '本月':
        return _day_start(now.date().replace(day=1))
    if text == '上月':
        last_month_end = now.date().replace(day=1) - timedelta(days=1)
        return _day_start(last_month_end.replace(day=1))
    if text == '下月':
        nxt = (now.date().replace(day=28) + timedelta(days=7)).replace(day=1)
        return _day_start(nxt)
    if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', text):
        year, month, day = (int(part) for part in text.split('-'))
        return _day_start(datetime(year, month, day).date())
    parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed


def resolve_window(spec, now):
    """校验单个窗口，返回 {label, start, end}；非法时抛 ValueError。

    spec 支持 {'start','end','label'} 或单个自然日字符串（如“昨天”）。"""
    if isinstance(spec, str):
        spec = {'start': spec, 'end': None}
    if not isinstance(spec, dict):
        raise ValueError('窗口格式无效')
    label = spec.get('label')
    if not isinstance(label, str) or not label.strip():
        label = None
    else:
        label = label.strip()[:24]
    start = _parse_boundary(spec.get('start'), now, 'start')
    raw_end = spec.get('end')
    end = _parse_boundary(raw_end, now, 'end') if raw_end else None
    if end is None:
        # 零点起点缺省为该自然日 [00:00, 次日00:00)；带时刻的起点必须给终点。
        if (start.hour, start.minute, start.second, start.microsecond) != (0, 0, 0, 0):
            raise ValueError('请提供结束时间')
        end = start + timedelta(days=1)
    if start >= end:
        raise ValueError('开始时间必须早于结束时间')
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError('时间范围应在90天以内')
    return {'label': label, 'start': start, 'end': end}
