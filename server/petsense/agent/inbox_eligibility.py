"""W12 中心策略：今日预览与 W13 推送候选的**唯一**判定处（agent-w12-spec.md）。

本模块是 W12 与后续 W11（个人变化高相关）、W13（推送）之间的窄类型合并点：
- 事实与状态由服务端代码给出（来源类型、来源当前状态、是否已交付、严重度）；
- W11 只写「冻结证据 + 明确相关性」两项，不解释其含义；
- 今日预览与 W13 推送候选都读同一份策略输入，口径不会分叉。

产品口径（**已由负责人确认，见 spec D7**）：
- 关键设备告警 = 仍开放且严重度为 warning/critical 的设备告警；**info 级设备告警
  （如失联 data_stale）永远只在收件箱**，不进今日预览、也不是推送候选。
- 「关键设备告警是否只算 critical」曾是待确认项，现已确认为 warning+critical；
  口径集中在 `KEY_DEVICE_SEVERITIES` 一处，改动只影响这一处。

推送口径（W13 D7）：
- 设备告警与到期回信**不受安静档影响**（安静档只约束主动会话，不约束这两类推送）；
- 学习完成与一般关怀**一律不是推送候选**；
- 「个人高相关」路径要求三件事同时成立：W11 冻结的证据 + 模型明确给出 'high' +
  W09 已批准交付。缺失相关性**不得**默认按 high 处理（缺失即不成立）；
- 历史补入（backfilled）不追溯推送：补入是站内历史，不产生系统通知。
"""
from dataclasses import dataclass, replace

# 来源类型（spec 的四类来源）
SOURCE_DEVICE_FACT = 'device_fact'   # 设备事实（W09 硬件规则主题）
SOURCE_CARE = 'care'                 # 关怀（W09 回顾发现主题）
SOURCE_REPLY = 'reply'               # 到期回信（W08 委托交付）
SOURCE_LEARNING = 'learning'         # 学习完成（W05→W09 转换）

SOURCE_KINDS = (SOURCE_DEVICE_FACT, SOURCE_CARE, SOURCE_REPLY, SOURCE_LEARNING)

# 预览优先级（D2）：仍开放的关键设备告警 → 到期回信 → 可预览的关怀
PRIORITY_DEVICE = 1
PRIORITY_REPLY = 2
PRIORITY_CARE = 3
PRIORITY_LEARNING = 4

# D7（已确认）：关键设备告警 = warning + critical；info 级只在收件箱
KEY_DEVICE_SEVERITIES = ('critical', 'warning')

# W09 主题 kind → 收件箱来源类型
TOPIC_KIND_TO_SOURCE = {
    'device': SOURCE_DEVICE_FACT,
    'observation': SOURCE_CARE,
    'learning': SOURCE_LEARNING,
}

# W08 交付 kind → 收件箱来源类型（委托条件提醒不属于 W12 四类来源，不入箱）
DELIVERY_KINDS = ('letter', 'degraded_letter')

# W13 D7：安静档不影响的来源类型
QUIET_UNAFFECTED_KINDS = (SOURCE_DEVICE_FACT, SOURCE_REPLY)

# W09 事件的稳定 request key 与阶段（用于精确匹配来源→回合身份）
EVENT_REQUEST_PREFIX = 'care:event:'
EVENT_PHASES = ('open', 'escalate')
REVIEW_REQUEST_PREFIX = 'care:review:'
REVIEW_KINDS = ('daily', 'weekly')


def event_request_ids(topic_id, phase=None):
    """W09 事件交付的真实 request id（精确匹配，不用 LIKE 前缀匹配）。"""
    phases = (phase,) if phase else EVENT_PHASES
    return ['%s%d:%s' % (EVENT_REQUEST_PREFIX, topic_id, item) for item in phases]


def review_request_ids(pet_id, window_key, kind=None):
    """W09 回顾交付的真实 request id（日/周两种口径都列出，按真实 key 精确匹配）。"""
    kinds = (kind,) if kind else REVIEW_KINDS
    return ['%s%s:%d:%s' % (REVIEW_REQUEST_PREFIX, item, pet_id, window_key) for item in kinds]


@dataclass(frozen=True)
class Eligibility:
    """一条收件箱条目的资格快照（持久化字段的不可变视图）。"""

    source_kind: str
    priority: int = PRIORITY_CARE
    previewable: bool = False
    published: bool = False
    turn_id: int | None = None
    relevance: str = ''             # W11 明确相关性：'high' / 'normal' / ''（缺失）
    frozen_evidence: bool = False   # W11 已冻结证据
    delivery_approved: bool = False # W09 已批准交付
    high_relevance: bool = False    # W11 写入的高相关标记（最终判定结果）
    push_candidate: bool = False
    backfilled: bool = False
    reason: str = ''

    def with_personal(self, *, relevance=None, frozen_evidence=None,
                      delivery_approved=None) -> 'Eligibility':
        """W11 合并入口：只写三项输入，其余维度不动；推送候选按中心策略重算。"""
        updated = replace(
            self,
            relevance=self.relevance if relevance is None else relevance,
            frozen_evidence=(self.frozen_evidence if frozen_evidence is None
                             else bool(frozen_evidence)),
            delivery_approved=(self.delivery_approved if delivery_approved is None
                               else bool(delivery_approved)),
        )
        high = (updated.frozen_evidence and updated.relevance == 'high'
                and updated.delivery_approved)
        updated = replace(updated, high_relevance=high)
        return replace(updated, push_candidate=policy_input(updated).push_candidate())

    def as_dict(self) -> dict:
        return {'sourceKind': self.source_kind, 'priority': self.priority,
                'previewable': self.previewable, 'published': self.published,
                'turnId': self.turn_id, 'relevance': self.relevance,
                'frozenEvidence': self.frozen_evidence,
                'deliveryApproved': self.delivery_approved,
                'highRelevance': self.high_relevance, 'pushCandidate': self.push_candidate,
                'backfilled': self.backfilled, 'reason': self.reason}

    @classmethod
    def from_dict(cls, data) -> 'Eligibility':
        data = data if isinstance(data, dict) else {}
        source_kind = str(data.get('sourceKind') or SOURCE_CARE)
        if source_kind not in SOURCE_KINDS:
            source_kind = SOURCE_CARE
        try:
            priority = int(data.get('priority', PRIORITY_CARE))
        except (TypeError, ValueError):
            priority = PRIORITY_CARE
        turn_id = data.get('turnId')
        return cls(source_kind=source_kind, priority=priority,
                   previewable=bool(data.get('previewable')),
                   published=bool(data.get('published')),
                   turn_id=turn_id if isinstance(turn_id, int) else None,
                   relevance=str(data.get('relevance') or ''),
                   frozen_evidence=bool(data.get('frozenEvidence')),
                   delivery_approved=bool(data.get('deliveryApproved')),
                   high_relevance=bool(data.get('highRelevance')),
                   push_candidate=bool(data.get('pushCandidate')),
                   backfilled=bool(data.get('backfilled')))


@dataclass(frozen=True)
class PolicyInput:
    """中心策略的输入：来源事实 + W11 冻结的三项判断。"""

    source_kind: str
    severity: str = ''
    source_status: str = ''
    published: bool = False
    relevance: str = ''
    frozen_evidence: bool = False
    delivery_approved: bool = False
    backfilled: bool = False
    proactivity: int = 1

    def is_key_device_alert(self) -> bool:
        """仍开放的关键设备告警（口径集中处，待负责人确认）。"""
        if self.source_kind != SOURCE_DEVICE_FACT:
            return False
        return (self.source_status in ('open', 'ongoing')
                and self.severity in KEY_DEVICE_SEVERITIES)

    def preview_tier(self):
        """今日预览档位；None 表示不进入今日预览（D2）。"""
        if self.source_kind == SOURCE_DEVICE_FACT:
            return PRIORITY_DEVICE if self.is_key_device_alert() else None
        if self.source_kind == SOURCE_REPLY:
            return PRIORITY_REPLY if self.published else None
        if self.source_kind == SOURCE_CARE:
            if not (self.published and self.source_status in ('open', 'ongoing')):
                return None
            return PRIORITY_CARE
        return None   # 学习完成只进收件箱

    def push_candidate(self) -> bool:
        """W13 推送候选（中心策略）。"""
        if self.backfilled:
            return False               # 历史补入不追溯推送
        if self.source_kind in QUIET_UNAFFECTED_KINDS:
            if self.source_kind == SOURCE_DEVICE_FACT:
                return self.is_key_device_alert() and self.published
            return self.published      # 到期回信：已交付即候选（安静档不影响）
        # 学习完成与一般关怀：只有「个人高相关」路径成立才是候选
        return bool(self.frozen_evidence and self.relevance == 'high'
                    and self.delivery_approved and self.published)


def policy_input(eligibility: Eligibility, *, severity: str = '', source_status: str = '',
                 proactivity: int = 1) -> PolicyInput:
    """由条目快照 + 来源当前状态构造策略输入。"""
    return PolicyInput(source_kind=eligibility.source_kind, severity=severity,
                       source_status=source_status, published=eligibility.published,
                       relevance=eligibility.relevance,
                       frozen_evidence=eligibility.frozen_evidence,
                       delivery_approved=eligibility.delivery_approved,
                       backfilled=eligibility.backfilled, proactivity=proactivity)


def push_candidate(policy: PolicyInput) -> bool:
    return policy.push_candidate()


def preview_tier(policy: PolicyInput):
    return policy.preview_tier()


def default_priority(source_kind: str) -> int:
    return {SOURCE_DEVICE_FACT: PRIORITY_DEVICE, SOURCE_REPLY: PRIORITY_REPLY,
            SOURCE_CARE: PRIORITY_CARE, SOURCE_LEARNING: PRIORITY_LEARNING}.get(
        source_kind, PRIORITY_CARE)
