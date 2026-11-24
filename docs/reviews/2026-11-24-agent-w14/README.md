# W14 体验合流验收截图（2026-11-24）

对应 spec：`docs/agent-w14-spec.md` §8。全部为 iOS 模拟器实拍（设备分辨率），按编号命名。

**环境**：主验收机 `W14-验收`（402×874 pt），紧凑机 `PetSense-Final`（390×844 pt）；
Expo Go + Metro 8087，后端接生产 `http://81.71.71.31`（真实模型），账号 `petsense`。
项圈自 11-20 01:22 起离线，因此没有个人变化与日记数据。

**两批截图要分开看**：

- `1-` ~ `23-`：**对比度修复后**的构建，本轮走查所拍，是当前形态。
- `24-` ~ `31-`：**对比度修复前**的构建（Codex 中断前所拍），只作**卡片形态与输入区形态**证据，
  其中的品牌橙文字颜色已过时，不能用来判断当前配色。

| 文件 | 内容 | 对应验收项 |
| --- | --- | --- |
| `1-today-dark.jpg` | 今日页（深色）：预览卡、铃铛徽标、健康体征卡 | V5 / V8 |
| `2-today-light.jpg` | 今日页（浅色） | V5 / V8 |
| `3-partner-topic-dark.jpg` | 伙伴页话题视图（深色）：话题横幅、Markdown 正文 | V1 |
| `4-partner-topic-light.jpg` | 伙伴页话题视图（浅色） | V1 |
| `5-confirm-card-light.jpg` | 委托确认卡（待确认）：计划全文、窗口、确认/不要、调整入口 | V2 / S6 |
| `6-confirm-card-confirmed-light.jpg` | 同一张卡确认后的终态「已生效」，按钮收起 | V2 / S6 |
| `7-taskbook-delivered-light.jpg` | 委托簿：已交付态 | V4 |
| `8-taskbook-observing-light.jpg` | 委托簿：观察中态 + 暂停/取消委托 | V4 / S6 |
| `9-taskbook-observing-dark.jpg` | 委托簿（深色） | V4 |
| `10-notification-drawer-dark.jpg` | 通知抽屉（深色）：收件箱分区、未读圆点 | V5 |
| `11-inbox-light.jpg` | 收件箱页（浅色）：原生 Stack 页头、来源类别标签、已读时间 | V5 / K12 |
| `12-push-settings-light.jpg` | 通知推送页（W13 未部署时的降级态 + 站内收件箱入口） | V5 / W13 |
| `13-assistant-settings-light.jpg` | 助手设定（浅色）：语气详略与主动程度三档选择器 | V6 |
| `14-assistant-settings-dark.jpg` | 助手设定（深色） | V6 |
| `15-pet-profile-light.jpg` | 宠物档案（浅色）：生日精度、体重记录、项圈卡 | V6 |
| `16-records-light.jpg` | 记录页 | A11 |
| `17-story-create-light.jpg` | 发布故事页 | V7 |
| `18-nearby-light.jpg` | 附近页：半径/分区选中态与主按钮（对比度修复后） | V8 |
| `19-community-search-light.jpg` | 社区搜索：分类分段选中态（对比度修复后） | V8 |
| `20-community-segment-light.jpg` | 社区页「发现/关注」选中态（对比度修复后） | V8 |
| `21-profile-links-light.jpg` | 「我的」页品牌橙链接（对比度修复后） | V8 / K2 |
| `22-small-guest-profile-light.jpg` | 紧凑机未登录「我的」：🐾 不再被行高裁切 | K3 / V9 |
| `23-small-partner-dark.jpg` | 紧凑机伙伴页（深色）：Markdown、头像标签、输入区 | V9 / V3 |
| `24-web-source-card-light.jpg` | 网页来源卡（修复前构建） | V2 |
| `25-diary-result-card-light.jpg` | 日记结果卡（修复前构建） | V2 / S9 |
| `26-draft-result-card-light.jpg` | 社区草稿结果卡（修复前构建） | V2 / S9 |
| `27-profile-confirm-card-light.jpg` | 档案确认卡（修复前构建） | V2 / S2 |
| `28-delegation-confirm-card-light.jpg` | 委托确认卡（修复前构建） | V2 / S6 |
| `29-status-reply-light.jpg` | 实时状态回答（修复前构建） | S1 |
| `30-input-keyboard-dark.jpg` | 键盘弹起时的输入区（修复前构建） | V3 / A10 |
| `31-input-attached-dark.jpg` | 带附件上传中的输入区（修复前构建） | V3 |
