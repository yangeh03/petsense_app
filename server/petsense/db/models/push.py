"""W13 原生推送（首版仅 iOS）：安装实例绑定、用户开关与可审计投递记录。

三张表：

- ``push_preferences``：用户级开关（默认关闭）。D3 要求用户在设置里主动开启后
  才申请系统权限并注册；关闭时服务端停止一切发送。
- ``push_installations``：安装实例（客户端生成的稳定 ID）。同一安装换账号时，
  归属整体迁移，旧归属的待发件立即从发送集合剔除（``state='revoked'``）。
  token 轮换只换 ``expo_token``，安装实例 ID 不变——去重键按安装实例而非 token，
  因此轮换不会造成重复投递。
- ``push_deliveries``：``(收件箱条目, 安装实例)`` 维度的投递审计行。去重键
  ``inbox_item_id + ':' + installation_id`` 唯一。状态机见 agent/push.py。

本包不保存任何凭据明文：``expo_token`` 是设备投递所需的推送地址（等价于设备
句柄），仅用于向 Expo Push Service 提交消息，日志中一律不打印。
"""
from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from petsense.db.base import Base, UTCDatetime


class PushPreference(Base):
    """用户级推送开关：一行一个用户，懒创建。

    enabled 默认 0（关闭）。服务端开关（按平台）与用户开关是两件事：
    服务端开关默认关闭由环境变量控制，用户开关默认关闭由本行控制。
    """
    __tablename__ = 'push_preferences'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    enabled = Column(Integer, nullable=False, default=0)
    updated_at = Column(UTCDatetime, nullable=False)


class PushInstallation(Base):
    """安装实例：客户端稳定 ID + 当前 token + 归属用户。

    state: active（可发送）/ revoked（用户退出或换账号后停用）/ unregistered
    （服务方回执判定 token 已失效，或同 token 被新安装实例接管）。
    permission_status: granted / denied / revoked / undetermined / unavailable。

    **token 只保留当前一枚**：审计与比较一律走 ``token_digest``（SHA-256）与
    ``token_version``，不把历史 token 明文留在库里（``expo_token`` 只有一个当前值，
    换绑时清空）。同一个 Expo token 只能绑定一个安装实例——同 token 再次注册时，
    旧安装实例立即停用并从发送集合剔除，避免同一台设备重复收到两份、或把旧账号
    的消息推给新主人。
    """
    __tablename__ = 'push_installations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    installation_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    platform = Column(String(16), nullable=False, default='ios')
    expo_token = Column(String(255), nullable=False, default='')
    token_digest = Column(String(64), nullable=False, default='')
    previous_token_digest = Column(String(64), nullable=True)
    token_version = Column(Integer, nullable=False, default=1)
    state = Column(String(16), nullable=False, default='active')
    permission_status = Column(String(16), nullable=False, default='undetermined')
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    last_seen_at = Column(UTCDatetime, nullable=True)


class PushDelivery(Base):
    """投递审计行：每个（收件箱条目, 安装实例）一行。

    state 机（agent/push.py 强制）：
    pending → sending → sent → delivered
                        → failed（明确失败，不再重试）
                        → rejected（服务方判定 token 失效，永久剔除）
                        → unknown（服务方是否接收未知；停止重试避免重复）
    pending → suppressed（按资格/安静档/开关抑制，从未发出）
    """
    __tablename__ = 'push_deliveries'
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_key = Column(String(160), nullable=False, unique=True)
    inbox_item_id = Column(String(64), nullable=False, index=True)
    installation_id = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    platform = Column(String(16), nullable=False, default='ios')
    category = Column(String(24), nullable=False)
    state = Column(String(24), nullable=False, default='pending')
    reason = Column(String(64), nullable=True)
    expo_ticket_id = Column(String(64), nullable=True)
    expo_receipt_id = Column(String(64), nullable=True)
    ticket_status = Column(String(16), nullable=True)
    receipt_status = Column(String(16), nullable=True)
    error_code = Column(String(64), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_attempt_at = Column(UTCDatetime, nullable=True)
    payload_digest = Column(String(64), nullable=True)
    token_digest = Column(String(64), nullable=True)   # 发送时所用 token 的摘要（非明文）
    token_version = Column(Integer, nullable=True)     # 发送时所用 token 的版本号
    meta = Column(Text, nullable=False, default='{}')
    last_error = Column(Text, nullable=True)
    created_at = Column(UTCDatetime, nullable=False)
    updated_at = Column(UTCDatetime, nullable=False)
    sent_at = Column(UTCDatetime, nullable=True)
    finished_at = Column(UTCDatetime, nullable=True)
    __table_args__ = (Index('ix_push_deliveries_state_next', 'state', 'next_attempt_at'),)
