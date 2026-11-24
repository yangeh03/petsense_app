"""W03 助手设定服务：持久化、校验与注入文案（agent-w03-spec.md §3.4/§3.6）。

四字段全直写（D3）：明确指令即改 + 回显，无确认卡；proactivity 字段先持久化、W09 生效。
行按 user_id 懒创建，无行时按 DEFAULTS 渲染。
"""
from petsense.db import iso_z, utc_now
from petsense.db.models import CompanionSettings

DEFAULTS = {'assistantName': '照料助手', 'ownerTitle': None,
            'verbosity': 'concise', 'proactivity': 1}
VERBOSITY_VALUES = ('concise', 'detailed')
PROACTIVITY_VALUES = (0, 1, 2)


def public(row):
    """序列化为 App/工具共用的 camelCase 形态；无行时按默认值。"""
    if row is None:
        return dict(DEFAULTS)
    return {'assistantName': row.assistant_name, 'ownerTitle': row.owner_title,
            'verbosity': row.verbosity, 'proactivity': row.proactivity,
            'updatedAt': iso_z(row.updated_at)}


def load(session, user_id):
    return public(session.get(CompanionSettings, user_id))


def validate_patch(data):
    """白名单四字段校验；返回 (字段列名, 值) 列表。ownerTitle 空串 = 清除。"""
    if not isinstance(data, dict) or not data or set(data) - set(DEFAULTS):
        raise ValueError('设定字段无效')
    columns = {'assistantName': 'assistant_name', 'ownerTitle': 'owner_title',
               'verbosity': 'verbosity', 'proactivity': 'proactivity'}
    patch = []
    for key, column in columns.items():
        if key not in data:
            continue
        value = data[key]
        if column == 'owner_title':
            if value is None or (isinstance(value, str) and not value.strip()):
                patch.append((column, None))
                continue
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20:
                raise ValueError('对主人的称呼需为 1—20 个字符')
            patch.append((column, value.strip()))
        elif column == 'assistant_name':
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20:
                raise ValueError('助手名字需为 1—20 个字符')
            patch.append((column, value.strip()))
        elif column == 'verbosity':
            if value not in VERBOSITY_VALUES:
                raise ValueError('语气详略仅支持简洁或详细')
            patch.append((column, value))
        else:
            if value not in PROACTIVITY_VALUES:
                raise ValueError('主动程度仅支持 0、1、2 三档')
            patch.append((column, int(value)))
    return patch


def persist(session, user_id, patch):
    """upsert 设定行；patch 为 validate_patch 的输出。"""
    row = session.get(CompanionSettings, user_id)
    if row is None:
        row = CompanionSettings(user_id=user_id, updated_at=utc_now())
        session.add(row)
    for column, value in patch:
        setattr(row, column, value)
    row.updated_at = utc_now()
    session.flush()
    return row


def prompt_line(values):
    """注入系统提示的设定行（动态配置，不是事实快照；agent-w03-spec.md §3.6）。"""
    # ownerTitle 默认为空：不能把 None 插进提示（模型会照着把主人称呼成「None」）
    owner = values['ownerTitle']
    owner_clause = (f"称呼主人用「{owner}」（未设置则自然称呼）；" if owner
                    else '对主人的称呼未设置，自然称呼；')
    return (f"助手设定（当前生效）：称呼你为「{values['assistantName']}」；"
            f"{owner_clause}"
            f"回答详略：{'详细展开' if values['verbosity'] == 'detailed' else '默认简短，主人追问再展开'}。"
            f"正文中可用「{values['assistantName']}」自称；消息开头的身份标注仍固定用【照料助手】，"
            '不因改名改变（两位人格规则不变）。')
