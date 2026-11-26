# 俯视图虚拟区域与定位文字

## 本期需求

观察当前第一版俯视图，用可修改的多边形划分家具附近和活动区域；每次定位除米制 XY 坐标，还输出对应区域编号与文字。例如 `position: {x: 1.2, y: 0.8}` 与 `region: {id: "sofa-front", label: "沙发前"}`。这两个字段是输出格式示例，不是实际设备观测。

本期采用人工观察与确认，不引入自动家具识别模型，也不制作卡通底图。区域不会改变现有 UWB 解算、边界约束、点动画和底图。虚拟区域只表示命名空间，不能据此断言宠物站在家具上，亦不是可行走性规划。

## 第一版地图候选与用户确认版

`docs/uwb-regions-first-map-draft.json` 根据当前第一版 `reconstruction-room / v1` 的真实底图制作，包含沙发前、桌子旁、衣柜前、门口、客厅中央和右侧活动区。全部标记 `confirmed: false`，尚未作为真实语义启用。尤其衣柜和门口只是视觉猜测，须现场核对，可改为中性名称。

2026-11-26 用户调整并导出确认版，原样保存为 `docs/uwb-regions-first-map-confirmed.json`，后续以此文件为准。共 7 个区域，全部确认；新增第 7 区编号 `area_1795631533959`，名称“衣柜前”。第 3 区和第 7 区使用同一文字但具有独立编号，保留用户命名，不擅自合并或重命名。所有顶点、优先级和确认状态均保留导出值。导出源文件 SHA-256：`bd53b7cfe528b1fd041460f76f42c1ba5ccfa40f748e9ab20c5f3f0452ccb05e`。

确认版通过地图指纹、全部多边形和边界检查：没有自交、越出地图或面积重叠；7 个区各取一个内部检查点，均返回对应区域编号与名称。记录见 `docs/uwb-regions-first-map-confirmed-check.json`。这是离线数据核对，不是设备实测，确认版需在功能部署后通过鉴权接口导入，发布结果以 PR/CI 和接口核对记录为准。

区域坐标与本版底图一致；图像像素 `(u,v)` 转为地图坐标为 `x=originX+u*metersPerPixel`、`y=originY+(height-v)*metersPerPixel`。不新增镜像、旋转或比例缩放。候选已裁剪到场地外轮廓内。未覆盖的空隙输出“未命名区域”，不会硬猜最近家具；可以补画区域。

离线核对页支持选择/重命名、拖动顶点、增加顶点、新增/删除区域、设置优先级、逐区勾选确认、点击测试位置与 JSON 导出/重新导入。底图和数据全部内嵌，无 HTTP 服务或外部依赖。生成方式：

```powershell
python tools/offline-map/region_review.py --bundle server/petsense/uwb/assets/reconstruction-room-v1 --draft docs/uwb-regions-first-map-draft.json --output uwb-region-review.html
```

导出只代表用户选择的确认状态，不会自动写入 App。页面先检查基本几何，服务器导入时完整检查凹口穿越、自交和场地轮廓。核对页是本地配置工具；App 默认仅显示坐标和区域文字，可展开虚拟边界。

## 后端地址与配置

- `GET /api/app/uwb/regions?deviceId=...` 读取当前地图的 `config`，包含 `mapId/mapVersion/mapFingerprint/revision/regions`。
- `PUT /api/app/uwb/regions?deviceId=...` 上传 JSON，沿用 `X-Petsense-Session` 登录鉴权，且只允许本人的设备。
- 请求字段：`mapId`、`mapVersion`、`mapFingerprint`、`baseRevision`、`confirmed: true` 和 `regions`。每个区域包含唯一 `id`、`label`、`polygon`、`priority`、`confirmed`。优先级越大越优先，同优先级按编号字典序固定选择。
- 最多 64 个区域、每区 3～256 个顶点；拒绝重复编号、自交、退化或穿出场地的轮廓，限制请求为 150 KB。地图或配置版本冲突返回 409；参数错误 400；跨账号访问 403；存储不可用 503。
- 修改前先 GET 获取 `revision` 作为 `baseRevision`；离线页初次草稿为 `none`，已经配置过时需要以最新 GET 结果更新它，不能覆盖他人新修改。空区域列表可清空配置，仍须确认和正确版本。
- 配置位于 `$PETSENSE_ROOT/uwb_regions/<范围摘要>.json`，范围包含设备、认领者、认领时间、地图指纹与 Tag。切图、换 Tag 或重新认领不会误用旧区域。使用原子写入和当前单进程锁；未来多进程需要共享写锁。
- 区域变动加入现有地图 `revision`，App 自动刷新，几何地图版本不变。`uwbPosition` 在鉴权 REST 和 WebSocket 中包含 `region`、`regionStatus`、`regionsRevision`；继续随整个 `uwbPosition` 从非鉴权旧链路脱敏。

## 判断和防抖

只使用有效的新测量更新区域，轮询和重复帧不累计防抖。进入区域首次可直接命名；跨区要求至少两次不同有效测量、持续至少 0.8 秒，并离开旧区边缘 0.15 m，避免零漂反复切换。重叠按优先级选择，家具附近区域可高于“客厅中央”。

状态为 `known`（区域确定）、`transition`（边界附近）、`unmapped`（未命名区域）、`unconfigured`（尚未设置已确认区域）、`uncertain`（质量不足）、`waiting`（等新定位确认）、`stale`（过期）。切换期间不把旧名称当作确定的当前位置；低质量或边界修正超过 0.5 m 时不确定命名。过期可以保留最后区域，App 明确标注“上次所在”；恢复、地图或配置改变时重新判断，不跨地图继承旧名称。

## 验收与当前状态

2026-11-26：实现代码，用户已调整并确认 7 个区域；已获用户授权部署；发布结果以 PR/CI 和正式接口为准，真机区域命中尚未验收。检查覆盖鉴权/设备归属、地图及配置冲突、凹形/自交边界、优先级、确认状态、防抖、过期、版本变更和客户端诚实文字。现场验收应在各区域中央和交界停留，观察文字与实际位置是否一致；需要第一版地图对应的基站标定，不能直接套用新场地布置。

验证结果：客户端 250 项测试、源码 lint、完整 typecheck、format:check 与 Web 构建通过；离线工具 23 项通过；Linux 隔离目录运行服务端 892 项，OK（5 项既有条件跳过），上传的被测文件已校验与本地一致，未修改正式数据库与地图绑定。离线页面通过实际浏览器文件模式检查：区域内点击命中、逐区确认导出、配置重新导入、错误地图拒绝导入，以及 390 px 手机宽度无横向溢出。以上属于代码与离线页面验证，不替代真实设备区域命中验收。

Agent 调用契约见 [UWB_REGION_AGENT_API.md](UWB_REGION_AGENT_API.md)，可复用现有 read_current_status 工具，无需另建区域推断。
