# 遛狗地图（广州南沙路段评分快照）

「位置」页室外视图的「遛狗地图」入口打开独立路由 `/dog-map`，展示**本地打包**的
广州南沙路段评分快照。它与项圈、实时遥测完全无关：未绑定设备或没有 GPS
定位时同样可用；返回即回到室外视图。

## 数据来源与快照

- 来源：CityScene 南沙演示地图，公开页面
  `https://qioyo.site/petsenseIMG/dogmap/`（663 个静态 `L.circleMarker` 路段评分点，
  名称 / 评分 / 等级 / 绿视率 / 人行道 / 交通暴露 / 人流占比 / 标签写在 tooltip 里）。
- 快照抓取日期：**2026-11-20**（本仓库打包版本的 `capturedAt`）。上游未提供
  原始采集时间，采集日期**未知**；页面为静态演示快照，不含实时路况。
- 评分与等级**原样保留源数据，不重算、不改写**（等级仅四种：适合遛狗 / 一般 /
  较差 / 不适合遛狗）。上游未命名的路段（`unnamed_*`）在数据中保留原始名，
  界面显示为「未命名道路」。

## 坐标系假设与验证

- 源页面把标记直接画在高德瓦片上（`webrd0{s}.is.autonavi.com`），且页内街景请求
  显式声明 `coordtype=gcj02ll`，因此**源坐标为 GCJ-02**。
- 导入时用标准 GCJ-02→WGS-84 **近似逆变换**（误差约 1–2 m），输出 WGS-84，
  与 App 其余部分（项圈 GNSS、OSM 瓦片）一致。**项圈坐标不参与任何转换。**
- 抽样核对（转换前后坐标对见 `tools/dog-map-data.test.cjs` 的固定断言）：
  - D49号路：GCJ-02 (22.792777, 113.547789) → WGS-84 (22.795723, 113.542658)，70 分，
    绿视率 13%、人行道 6%；
  - M90乡道：GCJ-02 (22.938126, 113.476743) → WGS-84 (22.940818, 113.471369)，50 分，源等级「较差」。
  - 转换后的点落在 OSM 瓦片的对应道路/桥位上（蕉门河车行桥等跨河点位与河道线吻合）。
- 若上游改为 WGS-84 或其他坐标系，导入工具的断言（高德瓦片 + gcj02ll 声明）
  会拒绝生成，防止在错误假设下出数据。

## 打包形态与运行时

- 数据：`app/src/generated/dog-map-data.ts`（生成文件，勿手改；版本号如
  `nansha-2026-11-20`、`capturedAt`、来源、覆盖范围 bbox 与紧凑点行）。已在
  `.prettierignore` 中排除。类型在 `app/src/types/dog-map.ts`，展开逻辑在
  `app/src/data/dog-map.ts`（数据版本与展示逻辑分离）。
- 运行时：与 GPS 地图同一套本地 Leaflet（`src/generated/leaflet-assets.ts`）
  和 WGS84 瓦片（`GPS_TILE_URL`，默认 OSM）。入口 `fitBounds` 到覆盖范围；
  低缩放级按网格聚合（显示点数/代表分），zoom ≥ 16 显示逐点分数标；点击标点
  在页内显示详情卡。离开覆盖范围（外扩 25%）提示「回到评分区域」一键复位。
  不加载任何外部脚本，不访问上游页面。
- 列表视图（`DogMapList`）不依赖底图：底图加载失败时仍可浏览全部 663 个路段
  并查看详情；列表选中会在回到地图视图时定位到该点。

## 更新快照

```sh
# 1) 抓取上游页面（或用 --source 指向已保存的 HTML）
curl -fsSL https://qioyo.site/petsenseIMG/dogmap/ -o /tmp/dogmap-source.html
cd app
# 2) 重新生成（会校验源坐标系声明、663 个点、坐标/评分/等级范围）
node tools/gen-dog-map.cjs --source /tmp/dogmap-source.html
# 3) 校验固定抽样点与完整性
npm test
# 4) 提交新生成的 src/generated/dog-map-data.ts，并更新本文件的抓取日期
```

上游结构变化导致提取数量不符时工具会失败退出；此时先人工核对源页面格式再调整
`tools/gen-dog-map.cjs` 的提取规则，不允许静默截断。

## 验证边界

`tools/dog-map-data.test.cjs`（来源元数据、663 行完整性、固定抽样行、展开逻辑）
与 `tools/dog-map-document.test.cjs`（本地 Leaflet、无外部脚本、fitBounds、聚合、
瓦片失败与覆盖范围上报）在每次 `npm test` 运行。模拟器端到端验收记录见 PR 描述；
真实行走路线推荐不在本功能范围内。
