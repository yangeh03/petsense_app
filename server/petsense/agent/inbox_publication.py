"""W11 → W12：已确认发布资格（confirmed publication eligibility）的严格解析。

W11 的「个人变化高相关」结论落在**不可变的运行轨迹**里（`agent_runs.conclusion`），
W12 的收件箱按同一份证据判定「个人高相关」推送候选。本模块是两者之间的唯一解析处，
判据全部是**严格相等**，不做任何默认或推断：

1. 主题带**稳定 run id**（W09 回顾主题在开启时补上，见 care_jobs.handle_review）；
2. 该运行属于**同一宠物**、形态为 `review`（周期回顾），不是对话或事件运行；
3. `conclusion.publication.provenance` **严格等于** `'model_high_accepted'`
   —— `'model_normal_accepted'`、`'legacy_default_high'`（模型未给档位时的默认高相关）、
   `'legacy_default_normal'` 一律**不成立**；
4. `conclusion.deliveredBy == 'w09_review'` 且窗口与主题依据里的 review 窗口一致；
5. 存在**已交付的 W09 回顾回合**：request id 精确匹配 `care:review:<kind>:<pet>:<窗口>`，
   且该回合的 care 块 `runId` 等于上面的运行 id（宠物 + 运行的**精确链接**）；
6. **主题本身是已交付的个人变化**：类别为 `personal_change`，主题上冻结的证据 id
   逐条出现在该回合的 `personal_change` 块里（主题 ↔ 回合的**精确链接**）。
   一次回顾会同时开出覆盖、观察等发现并挂同一个 run id——它们没有冻结证据，
   不因同一次回顾接受过别处的个人变化而被抬升。

任一条不成立即返回 None：正常/默认高相关、安静档（没有交付回合）、证据缺失、
运行与宠物不匹配、回合与运行不匹配、主题与回合证据不匹配，都**不会**被抬升为推送候选。

本模块不依赖 W13，也不做任何推送动作：只产出资格证据。
"""
import json

from sqlalchemy import select

from petsense.agent.inbox_eligibility import review_request_ids
from petsense.db.models.agent import AgentCareTopic, AgentRun, AgentTurn

ACCEPTED_PROVENANCE = 'model_high_accepted'   # 严格相等：模型明确 high 且发现被接受
DELIVERED_BY = 'w09_review'                    # 由 W09 回顾交付
REVIEW_FORM = 'review'                        # 周期回顾运行形态
CHANGE_CATEGORY = 'personal_change'           # 个人变化发现的类别（care.REVIEW_CATEGORIES）


def _review_window(topic):
    """主题依据里的回顾窗口键（review 条目）。"""
    for entry in json.loads(topic.evidence or '[]'):
        if not isinstance(entry, dict):
            continue
        if entry.get('kind') == 'review' and entry.get('window'):
            return entry['window']
    return None


def _turn_links_run(turn, run_id):
    """回合的 care 块是否精确指向该运行（W09 交付时写入 runId）。"""
    for block in json.loads(turn.blocks or '[]'):
        if not isinstance(block, dict) or block.get('type') != 'care':
            continue
        care = block.get('care') or {}
        if care.get('runId') == run_id:
            return True
    return False


def _topic_change_ids(topic):
    """主题上冻结的个人变化证据 id（handle_review 开题/合并提报时写入的 change 行）。"""
    ids = set()
    for entry in json.loads(topic.evidence or '[]'):
        change = entry.get('change') if isinstance(entry, dict) else None
        if isinstance(change, dict) and change.get('evidenceId'):
            ids.add(str(change['evidenceId']))
    return ids


def _turn_delivers_changes(turn, evidence_ids):
    """回合的个人变化块是否逐条交付了这些冻结证据（主题 ↔ 回合的精确链接）。"""
    if not evidence_ids:
        return False
    delivered = set()
    for block in json.loads(turn.blocks or '[]'):
        if not isinstance(block, dict) or block.get('type') != 'personal_change':
            continue
        change = block.get('change')
        if isinstance(change, dict) and change.get('evidenceId'):
            delivered.add(str(change['evidenceId']))
    return evidence_ids <= delivered


def resolve_accepted_publication(session, topic):
    """解析主题对应的「已确认高相关发布」证据。返回 dict 或 None。

    返回结构：{'runId','provenance','relevance','window','turnId'}
    """
    if topic is None or topic.kind != 'observation' or not topic.run_id:
        return None
    # 只有本身就是个人变化的主题才可能「个人高相关」：同一次回顾的覆盖/观察类发现
    # 带着同一个 run id，但没有冻结证据（W12 spec：个人高相关须 W11 冻结证据齐备）。
    change_ids = _topic_change_ids(topic)
    if topic.category != CHANGE_CATEGORY or not change_ids:
        return None
    run = session.get(AgentRun, topic.run_id)
    if run is None or run.pet_id != topic.pet_id or run.form != REVIEW_FORM:
        return None
    conclusion = json.loads(run.conclusion or '{}')
    publication = conclusion.get('publication') or {}
    if publication.get('provenance') != ACCEPTED_PROVENANCE:
        return None
    if conclusion.get('deliveredBy') != DELIVERED_BY:
        return None
    window = conclusion.get('window') or _review_window(topic)
    if not window or (conclusion.get('window') and conclusion.get('window') != window):
        return None
    if _review_window(topic) and _review_window(topic) != window:
        return None
    keys = review_request_ids(topic.pet_id, window)
    rows = session.execute(select(AgentTurn).where(
        AgentTurn.pet_id == topic.pet_id, AgentTurn.request_id.in_(keys))
        .order_by(AgentTurn.id.desc())).scalars().all()
    for turn in rows:
        if _turn_links_run(turn, run.id) and _turn_delivers_changes(turn, change_ids):
            return {'runId': run.id, 'provenance': ACCEPTED_PROVENANCE,
                    'relevance': publication.get('relevance') or 'high',
                    'window': window, 'turnId': turn.id}
    return None


def publication_inputs(session, topic):
    """给收件箱条目的三项输入：相关性、冻结证据、交付批准。

    不成立时返回 ('', False, False)（缺失即不成立，绝不默认按 high）。
    """
    evidence = resolve_accepted_publication(session, topic)
    if evidence is None:
        return '', False, False
    return 'high', True, True


__all__ = ['resolve_accepted_publication', 'publication_inputs',
           'ACCEPTED_PROVENANCE', 'DELIVERED_BY']
