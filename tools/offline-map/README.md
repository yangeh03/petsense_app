# 离线点云建图工具

此工具独立于 App 和 Flask。输入本地点云及人工确认的地面/尺度、锚点和 UWB 对齐文件，输出可供在线服务读取的版本化地图包。当前没有上传、后台任务、COLMAP 或 3DGS 服务端接口。

## 安装与生成

```sh
python3.12 -m venv tools/offline-map/.venv
tools/offline-map/.venv/bin/pip install -r tools/offline-map/requirements.txt
MPLCONFIGDIR=/tmp/petsense-matplotlib tools/offline-map/.venv/bin/python tools/offline-map/build.py \
  --pointcloud /data/Pointcloud.ply \
  --floor /data/uwb_to_rebuild_slice_transform.json \
  --anchors /data/anchors.json \
  --alignment /data/mirrorx_all_obstacles_raw_manual_final.json \
  --map-id reconstruction-room --version v2 \
  --output /data/maps/reconstruction-room-v2
```

输出目录必须不存在，避免覆盖已确认资产。支持当前提供的 binary little-endian XYZ float32 + RGB uint8 PLY。输入不能直接使用原始点云 Z 做高度；`--floor` 必须包含确认过的三维原点、正交地面基、向上法向与 `scale_prior`。

导出前检查地面基、三锚点非共线和 UWB 对齐变换；导出后复用服务器的纯地图契约，再检查 PNG 完整解码及轮廓覆盖范围。只在校验成功后发布输出目录，失败清理临时文件，不覆盖旧地图。图片上限为 4,000,000 字节、2000 万像素，单边不超过 10000；超限应调整 `--resolution`。工具从同一仓库的 `server/petsense/uwb/maps.py` 复用纯校验代码，需保留该源码路径，但不需要安装 Flask、数据库依赖、在线凭据或启动服务器。

也可独立检查已有地图包：

```sh
tools/offline-map/.venv/bin/python tools/offline-map/validate.py /data/maps/reconstruction-room-v2
```

默认俯视图保留离地 -0.05 至 1.20 米的点，1 厘米/像素，每像素绘制保留点中最高的点。`--min-height`、`--max-height`、`--resolution` 可以调整；这排除高处数据，不是天花板语义分类。墙体上部也会同时被裁掉。坐标尺度来自确认文件，不能将自动对齐的尺度当作独立实测真值。

可通行区域使用 0.08 至 `--obstacle-height`（默认 1 米）的点、3.5 厘米栅格。家具会影响边界；只保留一个可通行连通分量，导出的 polygon 不含内部孔洞。`diagnostics/navigation_grid.npz` 保留了障碍与可通行栅格，不能只凭外 polygon 判断内部可通行性。

## 输出契约

- `map.json`：mapId/version、米制 polygon、UWB 锚点、UWB→地图变换、背景原点/分辨率/尺寸、数据来源。
- `floorplan.png`：彩色俯视底图，图像 Y 向下；地图 Y 向上。
- `confirmed-floor.json`：生成时采用的地面坐标系，便于复核与复现。
- `polygon.json`、`diagnostics/`：几何、障碍栅格及预览；在线运行不需要加载 NPZ。

只需将 `map.json` 和 `floorplan.png` 一起发布到地图运行目录。底图像素 `(u,v)` 对应 `x=originX+u*resolution`、`y=originY+(height-v)*resolution`（像素中心取 `u+0.5,v+0.5`）。App 对背景、轮廓、锚点及定位点使用同一视图变换，不能独立拖动标记。

## 可选平面检测

```sh
tools/offline-map/.venv/bin/pip install -r tools/offline-map/requirements-plane.txt
tools/offline-map/.venv/bin/python tools/offline-map/rebuild_floor_slice_align.py --help
```

保留了原包的多平面检测和人工指定候选能力。原数据自动候选 P1 与人工确认 P0 不同，不能无检查地自动选地面。原脚本还需相机位姿与 UWB 轨迹，先在候选预览中确认地板/天花板，再运行 `build.py`。

来源：用户提供的 `uwb_full_server_deploy_20261028.zip`（SHA256 `98ccbf8fd4d150a184cae4cf214736d79ccc28eb3f25b582ecea39253bfeeea6`）。保留其楼面/栅格/轮廓处理脚本，新增版本化地图导出入口。原始 200 万点点云及实验轨迹未提交到 Git。
