# Agent 接入：实时坐标与区域文字

当前部署目标：用户确认的第一版地图 `reconstruction-room / v1`，7 个区域。此文为调用契约；发布状态以对应 PR 的 CI 和正式接口为准。真实项圈区域命中仍需现场验收。

## 现有 Agent 工具（优先使用）

工具名：`read_current_status`，参数例如 `{"minutes": 30}`。通过现有工具执行器调用，沿用当前用户和宠物上下文；不要另起一个 Python 进程或实例化定位服务，定位状态保存在正在运行的服务进程内。

`server/petsense/agent/registry.py` 的 `_execute_current_status` 已在校验宠物归属后调用当前 UWB 服务，原样返回 `result.location.indoor`。新增字段无需再对接另一套区域计算：

- `result.location.indoor.position`：米制地图坐标 `{x, y}`。
- `result.location.indoor.region.label`：可读文字，如“沙发前”。
- `result.location.indoor.region.id`：稳定区域编号，用于区分同名区域。
- `result.location.indoor.regionStatus`、`fresh`、`remainingMs`：判断文字是否可以当作当前位置使用。

`minutes` 只控制工具中的动作等历史统计窗口，室内位置始终是最新内存状态，不能据此回答过去 30 分钟分别在哪些区域，也不表示持续停留时长。本次不修改 Agent 的角色权限、派发或提示词，由 Agent 负责人按以下状态规则消费。

## REST 读取

```http
GET http://81.71.71.31/api/app/uwb/position?deviceId=<当前宠物绑定的设备ID>
X-Petsense-Session: <当前用户的登录会话令牌>
```

令牌沿用 App 登录接口 `POST /api/app/auth/login` 返回的 `token`，不要在代码、模型提示词或日志中写入真实令牌。设备必须属于当前用户；未登录返回 401，读取他人设备返回 403。不要固定使用测试账号、固定设备或管理员凭据为其他用户查询。

以下是协议示例，不是真实观测值，省略了其他质量字段：

```json
{
  "success": true,
  "deviceId": "example-device",
  "position": {
    "schemaVersion": 1,
    "deviceId": "example-device",
    "mapId": "reconstruction-room",
    "mapVersion": "v1",
    "fresh": true,
    "status": "ok",
    "position": {"x": 1.2, "y": 0.8},
    "region": {"id": "sofa-front", "label": "沙发前"},
    "regionStatus": "known",
    "regionsRevision": "<配置摘要>",
    "remainingMs": 1500,
    "expiresAt": "<本次测量过期时间>",
    "sequence": 25
  }
}
```

最外层 `position` 可能为 `null`（无标定地图、尚无位置、服务刚重启或设备身份变化等）。内层 `position.position` 也可为 `null`。这两种情况都不能输出确定区域文字。

## 文字使用规则

只有同时满足 `fresh === true`、`remainingMs > 0`、`regionStatus === "known"`、`region != null`、`status === "ok"` 时，才能表达“现在在沙发前”。收到后还要扣除网络及排队耗时；模型生成时间超过有效期时应重新查询，不能把缓存值继续当实时位置。

| regionStatus | 对用户表达 |
| --- | --- |
| `known` | 在仍有效的前提下使用 `region.label` |
| `transition` | “在区域边界附近，暂不能确定具体区域”；即使带旧 `region` 也不能当当前位置 |
| `stale` 或 `fresh=false` | 有旧名称则“最后一次定位在……，当前已过期”；否则“暂时没有有效定位” |
| `uncertain` | “定位质量不足，暂不能确定区域” |
| `unmapped` | “在地图未命名的区域”，不要猜最近家具 |
| `unconfigured` | “还没有设置已确认的区域” |
| `waiting` / `position=null` | “等待新的定位数据” |

区域文字来自已确认的虚拟边界，不能扩写成“坐在沙发上”“已回家”“走到了门口”或活动持续时长。第 3 区和第 7 区都叫“衣柜前”，但编号不同；需要区分时使用 `region.id`，不要自行重命名。

WebSocket 仍沿用鉴权 `/ws?token=...`，同一字段位于真实遥测帧的 `uwbPosition` 中；保持已有包解析方式，区域文字不要绕过设备归属校验。非鉴权旧链路不提供 `uwbPosition`。

## 配置读取与发布

`GET /api/app/uwb/regions?deviceId=...` 用于查看区域定义，**不是**实时位置接口；`PUT` 是修改配置接口，普通 Agent 查询不应调用。配置变更、切图或重新认领后，应重新读取当前状态，不能跨 `regionsRevision`、地图或设备复用旧区域。

本次要导入的确认文件为 `docs/uwb-regions-first-map-confirmed.json`；几何与内部检查点验证见对应 `-check.json`。该离线验证不能代替真实设备实测。
