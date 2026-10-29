# 绕边采集、点云对齐与实时位置

## 来源与实际边界

实现参考用户提供的 `uwb_full_server_deploy_20261028.zip`，尤其是 `uwb/memory_docs/HANDOFF.md` 最后的 **Default for future reconstruction-map alignment**。不能把旧的手工户型图约 92% 覆盖率当成点云图上的结果。

以下原脚本按字节保留在 `tools/offline-map/`，测试检查 SHA256：

| 原脚本 | SHA256 |
| --- | --- |
| `uwb_polygon_boundary_align.py` | `823209f09bd7e5a3432d6e58062110b77674f7115afabecfc7f919284d4149f2` |
| `rebuild_benchmark_auto_alignment.py` | `7d943b8a7d8359d54c3d56dcabdf1df25082abd742edfed1c12c8e9caf24e0a9` |
| `rebuild_evaluate_obstacle_alignment.py` | `fc5809b1b1e4a1fd1770d98009f018bbf79e741b43b8a58bc5df064656c8411d` |

`setup_align.py` 是集成适配器。默认调用原 `GridDistanceObjective` 与 `transform_from_params`，对 `raw_x_m/raw_y_m` 全部有效帧进行 X 镜像全局距离场搜索，尺度范围 `0.85–1.15`，450 个搜索点、45 次迭代、popsize 8、seed 20260730。原人工真值不进入自动优化目标。SciPy 版本会影响优化结果，自动候选必须预览确认。

旧多边形方法保留普通、X 镜像两个诊断候选：中值窗口 5、最大步长 0.75 m、最小步长 0.015 m、边界容差 0.32 m、3° 搜索、350 个搜索点，尺度固定 1。X/Y 镜像与 180° 旋转存在等价表示，这两个搜索已覆盖两种手性。

## App 操作

1. 完成 A0/A1/A2 触碰标定，保持基站位置不变。
2. 点击「开始绕边采集」，沿真实场地边缘慢走一圈，经过凹凸转角，回到起点附近。App 显示实际 UWB 轨迹、点数、路程、首尾距离与是否收到新鲜定位。
3. 点击「已回到起点，结束绕边」。至少 30 个有效点、3 m 轨迹；30 分钟或 20000 点后自动停止。只站在原地不能完成采集。
4. 从电脑导入点云和人工确认的地面参数；可选导入已确认的 UWB→地面变换。
5. 点击「生成俯视图并自动对齐」。处理在隔离子进程中进行，不阻塞 MQTT；最多一个后台任务、30 分钟超时。切出页面不影响任务。
6. 检查默认栅格候选，也可切换多边形或人工变换候选。确认轮廓、尺度、方向后启用。预览图必须成功加载显示；不符合质量门槛的结果不能启用。
7. 地图与当前设备 / Tag / 认领身份一起原子发布。位置页重新加载地图，由现有 UWB 解算器持续更新位置；失去新鲜测距时显示最后位置，不生成运动数据。

所有候选都需要最终视觉确认。这与 ZIP 中已经验证的“自动初值 + 人工确认”一致；不是无条件保证每个陌生环境都能唯一自动配准。点到障碍边缘的距离不是独立定位误差，尺度搜索也不是独立的米制标定。

## 人工文件地址

`GET /api/app/uwb/setup?deviceId=...` 的 `session.inputs` 返回各入口的绝对路径、是否存在以及 HTTP 地址，App 可复制路径。默认持久目录是 `<PETSENSE_ROOT>/uwb_setup/<认领身份哈希>/inputs/`，也可用 `PETSENSE_UWB_SETUP_ROOT` 指定。

| 入口 | 文件 | 含义 |
| --- | --- | --- |
| `pointcloud` | `Pointcloud.ply` | binary little-endian XYZ float32 + RGB uint8 点云，最大 2 GB |
| `floor` | `confirmed-floor.json` | 人工确认的地面原点、正交 XY 轴、向上法向、`scale_prior`，最大 64 KB |
| `alignment`（可选） | `alignment.json` | 原脚本 `uwb_trajectory_transform_v1` 变换：`scale`、`rotation_matrix`、`translation_m`，最大 64 KB |

**可选 alignment 的目标坐标必须是 confirmed-floor 米制坐标**，例如 ZIP 中已确认的 `mirrorx_all_obstacles_raw_manual_final.json`。不要直接导入手工户型图的变换，也不要重复应用最终图片摆正旋转。适配器统一组合地面→最终地图变换，PNG、轮廓、Anchor、轨迹和实时点使用同一坐标系。

文件可经 SFTP 放到返回的路径，或登录后用二进制 HTTP PUT 上传（不是 multipart）：

```sh
curl -X PUT 'https://YOUR_SERVER/api/app/uwb/setup/input/pointcloud?deviceId=YOUR_DEVICE' \
  -H 'X-Petsense-Session: YOUR_SESSION_TOKEN' \
  -H 'Content-Type: application/octet-stream' --data-binary @Pointcloud.ply
```

地面与可选变换分别使用 `/input/floor`、`/input/alignment`。代理须允许相应请求大小；也可直接使用 SFTP。输入在开始任务后独立快照，上传期间文件变化会拒绝任务；输入更新后须重新生成对齐，旧实时地图保留到新结果确认。任务处理中禁止 HTTP 覆盖输入。

## 接口与记录

全部接口要求当前用户已认领该设备：

- `POST /api/app/uwb/setup/walk/start`、`/walk/stop`，JSON `{deviceId}`。
- `PUT /api/app/uwb/setup/input/<kind>?deviceId=...`，二进制文件流。
- `POST /api/app/uwb/setup/align`，JSON `{deviceId}`。
- `GET /api/app/uwb/setup/preview?deviceId=...&jobId=...&candidate=grid|normal|mirror|manual`，返回同坐标系地图与预览轨迹。
- `POST /api/app/uwb/setup/publish`，JSON `{deviceId, jobId, candidate, confirmed: true}`，从当前预览启用；任务 ID 过期会拒绝。

状态为 `anchors_ready → walking → walk_ready → processing → alignment_review → published`。任务失败回到 `walk_ready`，可修正输入重试。服务重启会中断活动采集 / 处理，并保留原始数据。处理重启后须重新发起，不能悄悄沿用一个未完成任务。

每次绕边保存到 `walks/<walkId>/trail.csv` 和 `ranges.ndjson`，重采不覆盖旧文件。CSV 列兼容原轨迹记录器，含 `x_m/y_m`、`raw_x_m/raw_y_m`、三路滤波距离、序号、Tag、`valid_fix`、`pos_updated`。滤波和三边交会复用已经从原工程迁入的 `PositionEngine`；增加测距残差质量筛选，未达标行不计入有效点。

每次处理保存到 `jobs/<jobId>/`：输入快照、worker.log、native 地图 / 障碍栅格、各候选变换 / 报告 / 预览 / 地图包，以及 result.json。栅格候选要求点在栅格内比例 ≥95%、室内包络比例 ≥85%、边界距离中位值 ≤0.15 m、p90 ≤0.4 m、两轴轨迹跨度均至少覆盖室内范围 45%。这些是可预览启用的初筛门槛，不是精度保证。已人工确认导入的候选采用 ≥90% 栅格内 / ≥80% 室内包络门槛，仍要求再次预览确认。

## 部署

本次只更新功能分支。合并部署时，发布打包器把 `tools/offline-map/` 放入受管理的 `server/map_tools/`；沿用只允许 server 路径的 SSH 接收器，无需扩大接收权限。安装新增 matplotlib 依赖，并在 CI / 部署检查中运行离线工具测试。`PETSENSE_UWB_MAP_TOOLS`、`PETSENSE_UWB_MAP_PYTHON` 可覆盖工具目录及解释器。

运行目录、历史地图、手工文件不属于受管理代码路径。新地图只在离线验证、App 预览确认后更新 bindings.json；保留其他设备绑定，记录 ownerId / claimedAt，设备转让后不能复用旧认领者的新式地图绑定。现有单进程 MQTT 采集模型下使用进程锁；扩为多进程前需要共享任务调度 / 文件锁。
