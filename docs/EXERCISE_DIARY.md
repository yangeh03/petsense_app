# AI 运动日记

用户从「今日 → 今日记录」点击生成。服务端验证宠物归属，读取档案照和当天的有效动作观测，异步提交。档案照会缩至最长边 1600 px 并转为无原始元数据的 JPEG。步数采用 150 步／分钟的试点步频，由观测到的走动秒数估算。这个值未经项圈实机校准。

App 通过 `GET/POST /api/app/exercise-diaries/{pet_id}` 查看或提交任务。生成结果由服务端从 PetGIF 的鉴权海报接口读取，并保存在运行目录的 `diary-posters/`。App 通过 `GET /api/app/exercise-diaries/poster/{job_id}` 鉴权读取，保存或带入社区现有发布页。历史照片模板仍通过同一鉴权接口读取。

## 生产配置

PetSense 默认用 PetGIF 的 Marni 演示 UUID 挂载任务。可用 `PETSENSE_PETGIF_DIARY_PET_ID` 改用专用 UUID。两种配置都使用本次上传的宠物档案照片。通过已有 `PETGIF_KEY_FILE` 提供 AK，缺省读取服务进程用户的 `~/.petgif-api-key`。

PetGIF 托管的照片和原始海报可通过链接公开访问，App 在提交前提示。PetSense 日记历史和本地海报副本仍按账号鉴权。

生产验收使用已公开的演示宠物照片，从 App 检查任务提交、状态和取图。测试用运动观测不构成真实项圈步频验证。
