# W13 集成缝：W12 收件箱 ↔ W13 原生推送

W13 在 W12 仍在开发时独立实现。本文件固定两条缝的**确切契约**，合入时 W12 只需按
§1 与 §2 各接一处，不需要改动 W13 的其余代码。

方向是**单向**的：W12 → W13。W13 不读 W12 的表、不反向依赖 W12 的模块，也不猜测
W12 的 schema。

---

## §1 发布缝（服务端）：W12 建条目时调用 W13

### 调用点

W12 在**创建收件箱条目的同一个事务内**调用：

```python
from petsense.agent import push

result = push.publish_inbox_item(session, inbox_item_public_dict)
```

`session` 传 W12 自己的 session（同一事务）。W13 只用这个 session 写
`push_deliveries` 行，不新增其它副作用。

### 入参契约（`inbox_item_public_dict`）

W13 只读下列键；多出来的键一律忽略，缺可选键按「未标注」处理。

| 键 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `id` | str | 是 | W12 的稳定收件箱 ID（深链唯一键）。W13 原样存进投递行与通知载荷。 |
| `userId` | int | 是 | 条目拥有者。安装实例按这个用户匹配。 |
| `kind` | str | 是 | W12 来源类型，见下方取值表。 |
| `petId` | int | 否 | 宠物归属，用于审计与后续归属复核。 |
| `relevance` | str | 否 | `'high'` 表示模型判定高相关（个人变化必需）。 |
| `evidenceRef` | str | 否 | W11 冻结证据引用（个人变化必需）。 |
| `delegateConfirmed` | bool | 否 | 明确委托标记；显式 `false` 则到期回信不推。 |
| `migrated` | bool | 否 | 迁移补入条目：永不进入发送队列。 |

### `kind` 取值与推送分类

| W12 `kind` | W13 分类 | 可推 |
| --- | --- | --- |
| `device_fact`（别名 `device` / `device_alert`） | `device_alert` | 是——关键设备告警 |
| `due_letter`（别名 `letter` / `task_letter`） | `due_letter` | 是——明确委托的到期回信 |
| `care` + `relevance == 'high'` + 有 `evidenceRef` | `personal_change` | 是——高相关个人变化 |
| `care` 其余情形 | — | 否，只留站内 |
| `learning` | — | 否，只留站内 |
| 其它 / 缺 `id` / `migrated` | — | 否，只留站内 |

### W13 在缝内的判定（都在 W13 侧，W12 不需要重复判断）

- **安静档**：个人变化要求 W09 主动程度 > 0；安静档（0）一律不推个人变化。
  关键设备告警与到期回信不受安静档影响，但仍受用户开关与系统权限约束。
- **个人变化三门槛**：证据引用存在 + `relevance == 'high'` + 主动程度 > 0，缺一即抑制。
- **迁移补入**：`migrated` 或 `backfilled` 为真的条目直接跳过。
- **闸门关闭**：服务端平台闸门关闭时**不建投递行**（避免开闸后补发历史）。

### 返回值

```python
{'published': 2, 'reason': None, 'category': 'device_alert'}
```

`published` 是建出的投递行数（每个可发送安装实例一行）。`reason` 非空表示未推：
`not_pushable` / `platform_disabled` / `user_disabled` / `no_eligible_installation`。

### W12 侧要做的一件事

在 W12 创建收件箱条目的事务里加一行调用，并把 W12 的稳定 ID 与 W11 的高相关标记
放进上面那张表里的对应键。**不要**在 W12 侧实现任何推送开关或安静档判断——那些在
W13 侧，避免两处规则漂移。

---

## §1b 发送时的当前来源复核（服务端）：W13 反向读 W12

发布时的载荷只是**当时的快照**。发送前 W13 必须重新核对 W12 的**当前**状态——
否则告警已关闭、宠物已转走、条目已被替换或删除的情况下仍会推送。

### 安装解析器

合入 W12 时由 W12 安装一次：

```python
from petsense.agent import push

def resolve_current_source(session, inbox_item_id):
    """返回该收件箱条目的当前状态；条目不存在时返回 None。"""
    item = load_w12_inbox_item(session, inbox_item_id)   # W12 自己的查询
    if item is None:
        return None
    return {
        'ownerId': item.owner_id,
        'petId': item.pet_id,
        'kind': item.kind,                 # device_fact / due_letter / care / learning
        'backfilled': item.backfilled,     # 迁移补入
        'severity': item.severity,         # info / warning / critical
        'status': item.status,             # 设备告警当前状态：open / closed / recovered
        'topicId': item.w09_topic_id,      # 关怀/个人变化对应的 W09 主题
        'deliveryId': item.w08_delivery_id,  # 到期回信对应的 W08 交付
        'evidenceRef': item.w11_evidence_ref,  # W11 冻结证据引用
        'modelHighAccepted': item.model_high_accepted,  # 模型判定高相关且被接受
    }

push.set_inbox_source(resolve_current_source)
```

### W13 在发送前的复核（全部按当前来源，不看发布时的缓存）

| 情况 | 结果 |
| --- | --- |
| 解析器未接入 / 条目已删除 | 抑制（`source_unavailable`） |
| 条目是迁移补入 | 抑制（`backfilled`） |
| 拥有者已变（换账号/转赠） | 抑制（`ownership_changed`） |
| 条目类型已变（被替换） | 抑制（`source_kind_changed`） |
| 宠物已转到别人名下 | 抑制（`pet_scope_changed`） |
| 设备事实严重度是 `info` | 抑制（`info_not_pushable`，D8） |
| 设备告警已关闭/已恢复 | 抑制（`alert_closed`） |
| 到期回信找不到已交付的 W08 对象 | 抑制（`no_published_delivery` / `delivery_missing`） |
| 个人变化缺 W11 冻结证据 | 抑制（`missing_evidence`） |
| 个人变化缺模型高相关接受 | 抑制（`not_high_accepted`） |
| 个人变化的 W09 主题未发布/已关闭 | 抑制（`topic_not_published` / `topic_closed`） |
| 当前主动程度为 0（安静档） | 抑制（`quiet_mode`，只影响个人变化） |

W12 侧的返回值必须反映**当前**事实，不能复用创建时的缓存字段——尤其
`severity`、`status`（告警是否仍开放）、`owner_id` 与 `pet_id`。

---

## §2 深链缝（客户端）：W13 把稳定 ID 交给 W12 解析

### 现状

W13 的通知载荷只带一个稳定收件箱 ID：

```json
{ "data": { "inboxItemId": "<W12 稳定 ID>", "category": "device_alert" } }
```

客户端从载荷取出 ID 后交给 `app/src/services/inbox-deep-link.ts` 解析：

- 默认路由：`INBOX_DEEP_LINK_ROUTE`（当前 `/agent-inbox?item=<id>`）。
- 解析失败或路由不存在时回退 `INBOX_FALLBACK_ROUTE`（`/agent`，当前构建一定存在）。

### 合入 W12 时接一处

```ts
import { setInboxDeepLinkResolver } from '@/services/inbox-deep-link';

setInboxDeepLinkResolver((inboxItemId) => {
  // 用 W12 实际提交的路由与参数格式定位原始对象
  return `/<W12 收件箱路由>?item=${encodeURIComponent(inboxItemId)}`;
});
```

或者：W12 落地后直接把 `INBOX_DEEP_LINK_ROUTE` 改成 W12 的实际路由名。

### W12 侧负责的部分

- 用稳定 ID 解析出原始对象（设备事实 / 到期回信 / 个人变化 / 学习完成）。
- 未登录时的登录续接：W13 已把 ID 存进本地（`petsense.push.pendingInboxItemId`），
  登录后 W13 会取出并再次调用解析器；W12 的解析器需要能处理「已登录后定位」。
- 打开失败、跨账号、目标缺失时给出明确状态，**不**改变未读语义。

### 重复点击

W13 侧保证：载荷里只有稳定 ID，重复点击解析到同一个 ID，**不重复创建对象、不影响
未读语义**（未读由 W12 的已读接口负责）。W13 在处理完一次响应后清掉 last response，
避免重复导航。

---

## §3 迁移修订号（合入主干前必须改）

W13 的迁移文件是 `server/alembic/versions/w13_push_tmp.py`：

```
revision = 'w13_push_tmp'
down_revision = '0019'
```

这是**临时编号**：本工作树上一版迁移是 0019，W12 正在并行实现 0020。W13 按负责人
指示在本工作树内临时接在 0019 之后，以保证本分支可独立跑通全量单测与 alembic 往返。

合入主干时必须改为：

```
revision = '0021'          # 0020 已被 W12 占用，取下一个空号
down_revision = '0020'     # 指向 W12 的头
文件名 → 0021_agent_w13_push.py
```

不改 `down_revision` 会在主干产生两个 head（`0019 → w13_push_tmp` 与 `0019 → 0020`），
`alembic upgrade head` 行为不确定。改号后迁移文件内容无需其他调整。

---

## §4 服务端开关（默认全关）

| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `PETSENSE_PUSH_IOS_ENABLED` | 放开 iOS 推送 | **关闭** |
| `PETSENSE_PUSH_DISABLED` | 全局急停（任何平台都不发） | 未设置 |
| `PETSENSE_PUSH_MAX_ATTEMPTS` | 单条投递的有限重试次数 | 3 |
| `PETSENSE_PUSH_TIMEOUT_S` | 调 Expo 的超时 | 10 秒 |
| `PETSENSE_EXPO_ACCESS_TOKEN` | Expo 访问令牌（需要时） | 未设置 |

闸门关闭时：待发件保留在队列里（不消耗重试次数），开闸后仍可发送——这是「快速停发
手段」而不是「销毁队列」。`GET /api/app/push/status` 会返回当前闸门快照；客户端在
闸门关闭时**不申请系统权限**，设置页如实显示「推送暂未开放」。

## §4b token 绑定与隐私

- **一个 Expo token 只绑定一个安装实例**：同 token 再次注册（换账号、换安装 ID、
  重装后换了 install id）时，旧安装实例立即停用并清空 token，旧账号的待发件一并
  作废——同一台设备不会收到两份，新主人也不会收到旧账号的消息。
- **审计不落 token 明文**：安装实例只保留当前 token；历史 token 以
  `previous_token_digest`（SHA-256）与 `token_version` 留档。投递行只存
  `token_digest` + `token_version`，错误信息与日志一律经 `push_provider.scrub()`
  清洗（token 形态片段替换为 `[token]`）。
- **旧 token 的失效回执不得废掉新 token**：`DeviceNotRegistered` 只在「这次发送
  用的就是当前 token」（摘要与版本都相同）时才停用安装实例。
- **调用服务方时不持有数据库事务**：领取、复核、落库各自独立成短事务，外部调用
  在事务之外进行；写前意图（`pending → sending`）先提交，崩溃后被识别为未知。
- **回执窗口 24 小时**：超出窗口仍未拿到回执的按「未知」处理并停止重试；载荷
  超过 4KB 视为明确 schema 错误，不重试。

---

## §5 本包不做的事

- Android 远程推送与 Android 原生配置（application ID、通知渠道、FCM v1）不在本包
  范围；Android 与 Web 不注册、不申请系统权限，继续使用站内收件箱。
- 真实签名、APNs/FCM 凭据、EAS 登录、真机安装与真机推送验收由负责人执行。
- 服务端不预置或生成任何推送凭据，不在日志里打印 token。
