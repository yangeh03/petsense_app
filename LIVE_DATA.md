# PetSense 实时数据联调

默认链路：ESP32 → 4G → `81.71.71.31:80` MQTT 网关 → PetSense 服务 → `ws://81.71.71.31/ws` → App。

网页控制台：<http://81.71.71.31>。App 监测页只订阅数据，不会自动开始实验或 SD 卡采集。

## 本地运行

```sh
npm ci
npm run web
```

用 `EXPO_PUBLIC_TELEMETRY_URL` 覆盖 WebSocket 地址。手机真机不要使用电脑的 localhost。当前公网入口为 80，原来的 1883/8080 尚未对外开放；不要在 App 中放 MQTT 或 SSH 密码。

当前入口为明文 WS；HTTPS 托管的 Web App 必须改用支持 TLS 的 WSS 网关，否则浏览器会阻止混合内容。此版本验证范围为本地 Web 与公网真实数据连接，尚未验证 iOS/Android 真机发行包。

## 数据约定

- 服务端广播 `{topic, payload, received_at}`，payload 支持 JSON 字符串和对象。当前服务端将板端的 10 条批次展开后逐条推送。
- `ts_ms` 是设备启动以来的毫秒数，不是 Unix 时间；`received_at` 是云端接收时间。图表以首条接收时间锚定，后续按设备时间差排列，避免一秒批量上传挤在同一时刻。
- 保留健康、电量、IMU、音频 RMS/ZCR/40 维 Mel 和实验/SD 状态。音频文件分片不会当成遥测帧。
- `health.valid=false` 不进入健康曲线；心率、血氧和体温的零值显示为 `--`，不伪装成有效读数。
- 云端 WebSocket 已连接但 10 秒没有新数据时，界面显示等待设备。`sdLogging=false` 直接显示为未写入状态。
- 设备重启（运行时间回退）或设备 ID 改变时清空旧曲线。

## 验证

```sh
node tools/telemetry-check.cjs
node tools/telemetry-check.cjs --live
npm run lint
npm run typecheck
npm run format:check
npx expo export --platform web
```

`--live` 用 App 的真实解析器与连接服务收取至少 20 条递增时间戳样本，并验证 IMU、40 维音频特征、电量以及取消订阅后的连接释放。此检查只读，不会向板子发送录音或采集命令。
