# 主 Tab 顶部空白修复验收（2026-11-25）

**问题**：五个主 Tab 里，今日 / 位置 / 我的三页顶部多出一条空白带，内容没有顶到状态栏下沿；伙伴页与社区页正常。

## 根因

`expo-router` 的 `NativeTabs` 在 iOS 上默认打开 UIKit 的自动内容插入：
`NativeTabsView.ios.js` 把 `overrideScrollViewContentInsetAdjustmentBehavior` 设为 `true`，
react-native-screens 随即把「tab 屏幕第一子链首个滚动视图」的 `contentInsetAdjustmentBehavior`
由 RN 默认的 `never` 改回 `automatic`，系统自动把状态栏高度补进 `contentInset.top`。
而这三页自己又加了 `paddingTop: insets.top`，顶部被内缩两次。

- 今日 / 位置 / 我的：根节点是铺满全屏的 `ScrollView` → 自动调整生效 → 双倍留白
- 伙伴页：第一子链是固定顶栏 `View`，`FlatList` 是其兄弟节点 → 走不到，不受影响
- 社区页：`FlatList` 被顶栏容器（`paddingTop: insets.top + 8`）推离状态栏区 → 自动调整量为 0

运行时实测（本机 iPhone 17 Pro / iOS 26.5）：`insets.top = 62`、`insets.bottom = 83`。

## 修复

- `app/src/components/app-tabs.tsx`：给每个 `NativeTabs.Trigger` 加
  `disableAutomaticContentInsets={Platform.OS === 'ios'}`，关掉自动内容插入，安全区一律由页面自己管
  （Android 侧同一开关会去掉 react-native-screens 套的底部 `SafeAreaView`，故只对 iOS 生效）
- `app/src/app/(tabs)/community.tsx`、`app/src/app/(tabs)/profile.tsx`：关掉自动插入后底部不再自动补
  `insets.bottom`，两页改为手动补 `insets.bottom + BottomTabInset + Spacing.five`，与改前留白一致

## 验收

**通道**：视觉（模拟器截图，Expo Go SDK 58，iPhone 17 Pro / iOS 26.5，UDID `02A85AF7-0ABA-49E8-9D9C-D730EC05999B`，
Metro `exp://localhost:8101`）。改前 / 改后为同一 App 状态下的同设备对照。

判定方法：取页面首个内容元素的字形顶部像素（相对页面背景亮度差 > 60），换算为 pt（截图 3x）。

| 页面 | 改前 | 改后 | 变化 | 截图 |
|---|---|---|---|---|
| 今日 | 144.7pt | 82.7pt | **−62.0pt**（= `insets.top`） | `今日-改前.jpg` / `今日-改后.jpg` |
| 位置 | 154.3pt | 92.3pt | **−62.0pt** | `位置-改前.jpg` / `位置-改后.jpg` |
| 我的 | 156.7pt | 94.7pt | **−62.0pt** | `我的-改前.jpg` / `我的-改后.jpg` |
| 伙伴 | 85.3pt | 85.3pt | 0（未受影响） | `伙伴-改前.jpg` / `伙伴-改后.jpg` |
| 社区 | 79.0pt | 79.0pt | 0（未受影响） | `社区-改前.jpg` / `社区-改后.jpg` |

底部复核：位置页滚到底后，最后一行说明文字结束于 703pt，距标签栏顶（791pt）留白 88pt，未被遮挡
（`位置-改后-滚到底.jpg`）。社区页 / 我的页底部留白按算术与改前一致（改前 = 手动 70 + 自动 83 = 153pt，
改后 = 手动 `insets.bottom`(83) + `BottomTabInset`(50) + `Spacing.five`(20) = 153pt）。

**遗留**：本次验收在未登录状态（各页空态）下进行；社区页有帖列表时的底部留白与登录态下的「我的」页长列表未做视觉走查，
按上述算术等价推算。

## 踩坑记录

- `disableAutomaticContentInsets` 只在原生 Tab **挂载时**读取并下发给原生层，热更新 / 快刷不会重新应用；
  改完不重启 App 会看到「改了没效果」的假象。必须 `xcrun simctl terminate` 后重新 `openurl`。
- expo-router 58 的 `NativeTabTrigger` 只在静态分支（布局里的 Trigger）带上 `disableAutomaticContentInsets`，
  动态分支（屏幕内 Trigger）不带；原生 Tab 走的是动态路径，因此**不要指望在屏幕文件里写这个属性**——
  它必须写在布局文件（`app-tabs.tsx`）的 Trigger 上。
- 截图判定顶部位置时，深色模式下页面背景本身接近纯黑，用「亮度 < 阈值」找文字会全屏命中；
  应按「与背景亮度差」判定。
