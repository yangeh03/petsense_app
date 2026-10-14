# PetSense ESP32 固件

此目录是目前设备使用的板端代码整理版，面向 Waveshare ESP32-S3-A7670E-4G。保留现有采集和通信逻辑，只拆分配置，便于同学复现、接线和继续开发。硬件版本和接线以本项目实物为准。

## 目录

```text
firmware/
  README.md
  .gitignore
  petsense_esp32/
    petsense_esp32.ino   # 采集、AT/MQTT、录音上传、SD 与命令处理
    board_config.h     # 引脚、网络地址、设备标识、采集与上传参数
    secrets.example.h  # 密码模板
    secrets.h          # 自己创建，不提交 Git
```

## 1. 下载代码

```sh
git clone --branch feat/esp32-firmware https://github.com/yangeh03/petsense_app.git
cd petsense_app
```

本分支合并后也可以直接下载 main。没有 Git 的同学可以在 GitHub 选择 `feat/esp32-firmware` 分支，点击 **Code → Download ZIP** 后解压。以下命令都在仓库根目录执行；仅编译固件不需要安装 App 的 Node.js/npm 依赖。

## 2. 安装 Arduino CLI

验证环境：Windows、Arduino CLI **1.5.1**、ESP32 Arduino core **2.0.16**、FQBN `esp32:esp32:esp32s3`。

### Windows（推荐 ZIP）

1. 打开 [Arduino CLI 官方安装页](https://docs.arduino.cc/arduino-cli/installation/)，在 Download 中选择 Windows 64 bit；需要完全复现时在 [官方 Releases](https://github.com/arduino/arduino-cli/releases/tag/v1.5.1) 下载 1.5.1 的 Windows 64 位 ZIP。
2. 解压到例如 `C:\Tools\arduino-cli`，确认其中有 `arduino-cli.exe`。
3. 在 PowerShell 执行下面命令，使当前终端能找到 CLI：

```powershell
$env:Path = 'C:\Tools\arduino-cli;' + $env:Path
arduino-cli version
```

要长期使用，在 Windows「编辑账户的环境变量」中，把 `C:\Tools\arduino-cli` 添加到用户 `Path`，然后重新打开终端。也可以从官方安装页选择 MSI 安装包。

### macOS / Linux

安装了 Homebrew 时：

```sh
brew install arduino-cli
arduino-cli version
```

也可以从同一官方安装页下载对应系统和 CPU 架构的二进制文件，解压后放到 PATH。后面的编译命令通用；复制密码模板用 `cp`，串口名按本机调整。

## 3. 安装 ESP32 板卡包

新安装、尚无 CLI 配置时先执行 `arduino-cli config init`；已有配置时跳过，不要覆盖别人的设置。

```sh
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32@2.0.16
arduino-cli core list
```

确认列表中的 ESP32 版本是 **2.0.16**。当前代码使用 legacy I2S API，3.x 尚未验证，不要直接升级板卡包。安装和下载排障参考 [Espressif 官方安装文档](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html)。

`Wire`、`FS`、`SD_MMC` 和 `driver/i2s.h` 都来自 ESP32 core，不需要额外 `lib install`。MQTT 使用蜂窝模块 AT 命令，不依赖 PubSubClient/TinyGSM。

## 4. 填写配置

在 PowerShell 执行一次（已有 secrets.h 时不要覆盖）：

```powershell
Copy-Item firmware/petsense_esp32/secrets.example.h firmware/petsense_esp32/secrets.h
```

编辑 `secrets.h`，把 `REPLACE_WITH_YOUR_MQTT_PASSWORD` 替换为项目负责人私下提供的 MQTT 密码。模板只用于编译，默认占位密码无法登录。此文件及编译产物已经加入忽略规则，勿使用 `git add -f` 上传它们。

`board_config.h` 中的默认值：

| 参数              | 默认值                                            |
| ----------------- | ------------------------------------------------- |
| APN               | `ctnet`（本设备当前电信卡；换卡需核实运营商 APN） |
| MQTT 地址         | `81.71.71.31`                                     |
| MQTT 端口         | `80`，当前服务器的 TCP 网关入口                   |
| MQTT 用户名       | `petsense-device`                                 |
| Client ID         | `esp32-001`                                       |
| 数据中的 deviceId | `petsense-esp32-001`                              |
| 传感器 Topic      | `petsense/sensor/esp32-001`                       |
| 命令 Topic        | `petsense/device/esp32-001/command`               |

`80` 是目前已配置的 MQTT/HTTP 分流入口，不要自行改成 1883。多块板子同时连接时，至少为 Client ID、deviceId、传感器 Topic、命令 Topic 设置唯一且匹配的标识，并让服务器同步配置对应设备。当前网页主要面向 `esp32-001`。App 从 WebSocket 获取数据，不应内置 MQTT 密码。

## 5. 接线与串口

| 功能            | ESP32 GPIO / 参数                           |
| --------------- | ------------------------------------------- |
| PDM 麦克风      | CLK 13，DATA 14                             |
| IMU I2C         | SDA 1，SCL 7，地址 `0x23`                   |
| 健康模块 UART   | RX 40 ← 模块 TX；TX 41 → 模块 RX；9600 baud |
| 4G 模块 UART    | RX 17，TX 18；115200 baud                   |
| MAX17048 电量计 | SDA 15，SCL 16，地址 `0x36`                 |
| SDMMC（1-bit）  | CLK 5，CMD 4，D0 6，检测 46                 |
| UART0 调试      | TX 43，RX 44；921600 baud                   |

健康模块当前已接到 **40/41**，不是 SD 卡的 4/5。确认共地、供电与逻辑电平符合模块要求，蜂窝天线接 **MAIN**，不是 GNSS/NET 座。

```sh
arduino-cli board list
```

选择 ESP32 的 CH343/USB 串口。本机之前是 `COM10`，同学电脑上会变化；SimTech AT/NMEA/Diagnostics 串口是蜂窝模块端口，不能用于 ESP32 烧录。如果没有 CH343 串口，从 [Waveshare 板卡文档](https://docs.waveshare.net/ESP32-S3-A7670E-4G/Windows) 获取适合的驱动并检查 USB 数据线。

## 6. 编译、烧录、看日志

```sh
arduino-cli compile --fqbn esp32:esp32:esp32s3 --build-path firmware/petsense_esp32/build firmware/petsense_esp32
arduino-cli upload --port COM10 --fqbn esp32:esp32:esp32s3 --input-dir firmware/petsense_esp32/build firmware/petsense_esp32
arduino-cli monitor --port COM10 --config baudrate=921600
```

把 `COM10` 换成自己的端口。macOS 常见 `/dev/cu.*`，Linux 常见 `/dev/ttyUSB*` 或 `/dev/ttyACM*`。每次修改代码/密码后先重新编译，再上传；上述步骤使用的是同一个明确的 build 目录。

用 Ctrl+C 退出监视器，再次烧录前关闭占用串口的软件。连接失败时检查端口；必要时按住 BOOT、按一下 RESET 后松开 BOOT，再重试上传。

## 7. 验收链路

依次确认串口出现传感器初始化结果、SIM READY、运营商注册、`+CMQTTCONNECT: 0,0`、命令订阅成功、持续的 `MQTT batch publish OK`。仅编译通过不代表联网或 SD 写入成功。

打开 [当前服务器控制台](http://81.71.71.31)，检查接收时间持续变化。网页开启实验后，检查 `sdLogging=true`、`sdRows` 递增，并在停止实验后读卡确认 CSV 有实际数据。没有贴合健康模块时零值不是有效健康测量。

## 当前数据行为与限制

- IMU/音频特征目标每 100 ms 采样；板端最多攒 10 条、约每秒批量发一次。串行 AT 上传和重试可能造成实际速率下降，不是硬实时保证。
- 健康与电量的 SD 更新节奏为每秒一次，CSV 的 `health_fresh` / `battery_fresh` 区分新值；健康 UART 会持续服务。
- 麦克风为 16 kHz、16-bit 单声道，输出 RMS、ZCR、40 维 Mel。按需录音默认 3 秒，分片上传；当前 SD 记录不保存原始 WAV。
- SD 当前采用根目录下的 `petsense_<experiment_id>_samples.csv`，按事件列标注，不会为十个事件分别创建文件夹。主流程也未调用旧 manifest/WAV 创建函数；代码中保留的旧辅助函数不代表当前会生成这些文件。
- CSV 包含设备 `ts_ms`、实验/事件标识、音频特征、IMU、健康、电量。`ts_ms` 是启动后毫秒数，不是 UTC；云端 `received_at` 是接收时间，训练对齐时要保留两者及实验 ID。
- SD 定期 flush（配置为 5 秒），停止时关闭文件。突然断电或写入中拔卡仍可能丢失缓存、损坏 FAT；结束实验并确认停止后再断电/拔卡。本版本不承诺任意时刻拔卡数据完整。
- 默认 `LOCAL_SD_CAPTURE_MODE=false`，开机联网但不会自行开始实验；网页/命令才能启动实验。该开关设为 true 会启动本地采集并跳过蜂窝联网，不是开启双端采集的开关。
- MQTT/WS 入口目前是明文 TCP，未配置 TLS。分享源码只包含模板，不包含生产密码。

## 代码导航

在 `.ino` 中搜索函数名即可定位：`setup/loop`（启动与调度）、`initCellularNetwork/mqttConnect`（蜂窝联网）、`appendCloudBatchSample/publishCloudSnapshot`（批次遥测）、`captureAudioFeatureStep`（音频特征）、`captureAudioFileStep/processAudioFileUploadStep`（录音上传）、`initSdLogger/sdLogStep`（SD）、`serviceHealthModule`（健康串口）。

此次整理保留原有采集逻辑；进一步修复 SD 稳定性、时间同步或增加设备功能应单独提交、实机验证。
