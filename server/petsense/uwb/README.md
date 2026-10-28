# UWB 在线定位

仅接收现有 MQTT 遥测的 `uwb` 字段，不连接串口，不加载点云，不需要 Open3D/Matplotlib。`engine.py` 源自用户完整 UWB 包的纯二维定位核心；`service.py` 负责设备/所有者/Tag/标定状态隔离、去重和过期；`maps.py` 校验地图资产。

## 部署与启用

当前部署器管理整个 `petsense/` 目录，因此模块及内置地图样例会随原有 server 发布流程部署；不需要新增进程、端口或替换 wsgi.py。原有 `requirements.lock` 不变。

默认读取 `$PETSENSE_ROOT/uwb_maps/bindings.json`，也可通过 `PETSENSE_UWB_BINDINGS_FILE` 指定文件。没有文件或绑定时功能关闭，健康、音频等原有遥测继续运行；不自动认领设备或修改任何用户绑定。

先复制内置 `assets/reconstruction-room-v1/` 或离线工具新输出的地图目录到运行数据目录 `uwb_maps/`，再由管理员创建绑定，例如：

```json
{
  "devices": {
    "your-actual-device-id": {
      "directory": "reconstruction-room-v1",
      "tagId": 7
    }
  }
}
```

设备 ID、Tag ID 都必须替换为实物确认值，并确保 MQTT Topic 的设备 ID 与 payload.deviceId 相同。示例中的 7 是测试值，不代表用户实物 Tag。目录相对绑定文件，禁止越出地图根目录。

地图与设备绑定属于运行时配置，不由代码更新覆盖。新地图使用新目录和 version，以原子替换 bindings.json 方式切换。回滚时保留旧地图并切回旧绑定；若切到不支持本 schema 的旧服务器则禁用绑定。加载/切换是惰性的，新测距会创建对应引擎，不必重启在线服务。不可在保持 version 的情况下改动地图内容。

## 接口与状态

- `GET /api/app/uwb/map?deviceId=...`：登录且拥有设备时返回地图；内嵌 data URI 背景，避免 Web 图片请求不能携带自定义鉴权头。PNG 限制 4MB、2000 万像素，App 进入页面时获取一次，地图版本变化或手动重试时再获取。`background.byteLength` 和八位小写十六进制 `background.crc32` 描述同一份已校验图片快照，供客户端核对传输完整性；旧客户端可忽略新增字段。CRC32 不是密码学校验，不替代接口鉴权。
- `GET /api/app/uwb/map-version?deviceId=...`：同样要求登录及设备归属，返回 `{mapId, version, revision}` 或 null，不包含图片；不需要收到定位测量。revision 包含地图指纹及 Tag 绑定，App 聚焦位置页时每 5 秒检查，新增/切换/删除绑定自动更新，未变化时不重新下载 PNG。完整地图也返回同一 revision。旧服务器无此接口（404）时，App 回退为每 30 秒获取完整地图。
- `GET /api/app/uwb/position?deviceId=...`：当前定位状态；只在内存中保留，重启后等待新 MQTT，不伪造历史轨迹。
- 原有 MQTT WS 包在 `payload.uwbPosition` 附加相同字段；原始测距仍保留。App 用鉴权快照每秒刷新位置、在本地独立过期，拖动不影响数据。
- 原有 `/uwb/latest` 保持原测距查询语义。

未登录 401、访问他人设备 403、地图损坏 503、未绑定地图/尚无测距返回 null。设备归属或 claimed_at 变化会丢弃旧位置。首次部署先在隔离环境验证映射及已知点，确认后再绑定实物设备。

`ok`/`low_quality`/`waiting`/`stale` 由真实测距和质量决定。位置过期按服务端 monotonic deadline 在每次读取时计算，不依赖某条串口空闲或其他设备上报；客户端用 remainingMs 自行过期。灰色标记仅表示最后一次位置，低质量点不扩展轨迹。

重复 timer/sequence 不推进滤波或刷新 deadline；批内排队时间与 receivedMs 相对样本时间的年龄会扣除。当前旧固件只有 uptime，无法推断首次收到的旧批次经历了多少网络延迟；重启识别仍使用有限的 uptime 回退约束，后续应增加 boot_id 和可信采样时间。不要将它宣传为任意离线补传都可严格判新的协议。

在线计算要求配置的三个 anchorMask 位和距离有效。该算法使用二维距离模型，若 Anchor 与 Tag 高差显著，应另行核实测距的水平投影/三维补偿。单 Tag 固件不能无损保存多 Tag 流。

俯视背景是实际点云导出的示例；可通行外轮廓不是建筑墙体图，也不含内部障碍孔洞。样例部署不自动授权给任何用户。
