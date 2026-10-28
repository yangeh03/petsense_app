## 变更说明

位置页在完整接收、校验并成功显示底图后才提示已加载；地图绑定和版本变更不再依赖新的定位坐标。离线工具在发布地图包前完成与在线服务一致的校验，并按户型主要墙向自动摆正，图片、轮廓和 UWB 定位变换同步更新。

- 地图接口增加 PNG 字节数/CRC32；App 增加完整 PNG 校验、解码/显示检测、失败重试和可展开的底图信息。
- 新增鉴权 `/uwb/map-version`；位置页每 5 秒检查版本/Tag 绑定，未变更不重新下载图片，解绑清空旧地图。旧服务端 404 时兼容为每 30 秒获取完整地图。
- 离线导出复用服务器纯几何契约，检查锚点、变换、PNG 解码、图片上限、轮廓及旋转一致性；临时目录校验通过后才发布，保留独立 validate.py。
- 默认按长轮廓边摆正，低一致性回退到最小面积包围矩形并标记歧义；支持关闭自动及手动角度/90°修正。从原点云重新投影，米制比例和原 UWB Anchor 标定保持一致。

已同步 main 的 7b9ee8e，复用同伴新增的原生图片层与 backgroundImageRect，保留其回归测试。ESP32 继续采集回传 IMU/UWB，云端完成识别和定位；本 PR 不修改固件。当前内置地图与生产 bindings 保持原版本，新地图上线需用新 version 同时发布 PNG 和 map.json。

## 变更类型

- [x] 新功能（feat）
- [x] 修复缺陷（fix）
- [ ] 样式 / UI 调整（style / ui）
- [x] 重构（refactor）
- [x] 文档（docs）
- [x] 工程配置 / CI（chore）
- [ ] 其他：

## 自测清单

- [x] `npm run lint` 通过
- [x] `npm run typecheck` 通过
- [x] `npm run format:check` 通过
- [x] `npm test`：78 项通过
- [x] 服务端 unittest：174 项通过
- [x] 离线工具 unittest：16 项通过
- [x] Expo SDK 58 Web 导出通过
- [x] 在浏览器中手动验证主要流程
- [x] 深色模式显示正常

隔离账号/数据库下验证正常、慢网、截断、校验失败、解码失败及重试；无 UWB 测量时验证新增绑定、版本切换、Tag 变更、解绑，以及未变更时不重新下载 PNG。拖动、缩放、回正与图层叠加正常。

实际处理提供的 200 万点，自动旋转约 -9.8369°，输出 780 × 549。两次导出的 map.json、PNG、orientation.json、polygon.json 字节一致；关闭旋转时 PNG 与原 714 × 431 样例字节一致。核对全部 217 个轮廓顶点、三个 Anchor 和已知点变换。

- [ ] iPhone/Android 实机清晰度、手势及真实定位闭环待验收；浏览器验证不替代这些检查。

## 截图 / 录屏

截图使用实际点云生成的地图、隔离测试账号，无定位数据时显示等待项圈提供位置。

浅色：

<img src="https://github.com/yangeh03/petsense_app/blob/fb19e671b9d77d34fb05e952ea8fe5894d2476fb/docs/screenshots/uwb-map-light.png?raw=true" width="360" alt="浅色模式的摆正地图、加载状态和底图信息" />

深色：

<img src="https://github.com/yangeh03/petsense_app/blob/fb19e671b9d77d34fb05e952ea8fe5894d2476fb/docs/screenshots/uwb-map-dark.png?raw=true" width="360" alt="深色模式的摆正地图、加载状态和底图信息" />
