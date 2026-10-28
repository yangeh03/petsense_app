# 新环境定位接入：第一步，三基站触碰标定

本阶段接入 MQTT 实测距离 → 触碰采集 → MDS 基站坐标，以及 App「位置」页的标定按键和状态。后续的点云处理、绕边轨迹采集、自动配准与地图发布尚未接入此引导，不会把标定成功显示成实时定位已经完成。

## 操作

固定三个基站 A0、A1、A2，避免共线。在 App「位置」页填写 Tag ID（本次实验为 0），开始标定。依次将 Tag 贴近 A0、A1、A2，点击对应按钮并保持静止六秒。每次至少需要三个不同的有效测距帧。重采某个基站会清除它及其之后的采集结果，避免混用不同布局。

距离以米计算，A0 为原点、A1 沿正 x 轴、A2 在正 y 半平面；镜像与地图方向留待轨迹配准处理。当前假定三个基站与触碰位置处于同一测距平面。高度不一致会导致水平尺度误差，暂不做高度补偿。

## 人工文件入口

后端运行目录默认 `<PETSENSE_ROOT>/uwb_setup/`，可用 `PETSENSE_UWB_SETUP_ROOT` 指定独立持久目录。创建任务后，接口返回该设备、用户及认领记录专属的绝对地址：

- `session.files.pointcloud`：`inputs/Pointcloud.ply`，人工上传的原始点云。
- `session.files.floor`：`inputs/confirmed-floor.json`，人工确认的地面原点、正交轴和尺度参数，格式见 `tools/offline-map/build.py`。

此阶段仅预留文件位置，不自动处理、删除或发布人工文件；现有离线地图工具仍可使用。服务部署必须持久化该目录。原始触碰测距保留在同目录 `touch.ndjson`；会话与基站结果保留在 `session.json`。服务重启会中断未完成触碰，App 提示重新采集。

## 接口

均要求 `X-Petsense-Session`，并校验设备当前归属。

- `GET /api/app/uwb/setup?deviceId=...`：查询任务（无任务返回 `session: null`），到期触碰在读取时结算。
- `POST /api/app/uwb/setup`：`{deviceId, tagId: 0}`，创建或恢复，重复调用不会覆盖原任务。
- `POST /api/app/uwb/setup/touch`：`{deviceId, anchor: 0|1|2}`，开始六秒采集。
- `POST /api/app/uwb/setup/cancel`：`{deviceId}`，取消当前触碰。

采集独立于既有地图绑定，只接收同一认领身份、同一 Tag 的新鲜遥测；按模块 timer/sequence 去重。触碰自身的零距离允许缺少有效掩码，其余两个基站必须有有效距离；Tag 与触碰基站距离不得超过 25 cm。波动超过 40 cm、双向测距差超过 35 cm、基站过近或接近共线均拒绝生成坐标。

当前采集器使用单服务进程内锁，与现有 MQTT 单进程接收方式一致；多进程部署前须将采集调度与锁迁入共享服务。
