# 俯视图虚拟区域与定位文字

## 本期需求

观察当前第一版俯视图，用可修改的多边形划分家具附近和活动区域；每次定位除米制 XY 坐标，还输出对应区域编号与文字。例如 `position: {x: 1.2, y: 0.8}` 与 `region: {id: "sofa-front", label: "沙发前"}`。这两个字段是输出格式示例，不是实际设备观测。

本期采用人工观察与确认，不引入自动家具识别模型，也不制作卡通底图。区域不会改变现有 UWB 解算、边界约束、点动画和底图。虚拟区域只表示命名空间，不能据此断言宠物站在家具上，亦不是可行走性规划。

## 第一版地图候选

`docs/uwb-regions-first-map-draft.json` 根据当前第一版 `reconstruction-room / v1` 的真实底图制作，包含沙发前、桌子旁、衣柜前、门口、客厅中央和右侧活动区。全部标记 `confirmed: false`，尚未作为真实语义启用。尤其衣柜和门口只是视觉猜测，须现场核对，可改为中性名称。

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

2026-11-26：实现代码与离线候选；尚未合并部署，本次候选需用户核对，真机区域命中尚未验收。检查覆盖鉴权/设备归属、地图及配置冲突、凹形/自交边界、优先级、确认状态、防抖、过期、版本变更和客户端诚实文字。现场验收应在各区域中央和交界停留，观察文字与实际位置是否一致；需要第一版地图对应的基站标定，不能直接套用新场地布置。
