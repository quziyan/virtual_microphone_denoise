# VibeCodingVirMic 发布记录 / Releases

供下载的安装包都在本 `dist/` 目录。安装方法见仓库根目录 `README.md` 的「在另一台 Mac 上安装」。

> 适用环境:**Apple Silicon Mac**。安装包未做 Apple 公证,首次打开请**右键 .pkg → 打开**,或 `sudo installer -pkg <包名> -target /`。安装需要一次管理员密码(用于安装 BlackHole 音频驱动)。

---

## v1.0.1 — 2026-06-09

**安装包:** `VibeCodingVirMic-Installer-1.0.1.pkg`

- **新增:** 应用内「检查更新 / Check for Updates」—— 启动后及每 24 小时自动比对 `appcast.json`,有新版菜单提示 + 系统通知。
- **新增:** 使用情况上报 —— 打开 / 开始 / 停止 / 切换降噪档 / 切换麦克风 / 打开设置 / 检查更新 / 退出,写入飞书多维表格(含上报时间、机器名称、IP 地址、上报类型)。
- **修复:** 重装后 Launchpad/Spotlight 出现多个副本 —— 安装前清除旧副本、构建产物不再被索引,保证干净替换。

## v1.0.0 — 2026-06-08

**安装包:** `VibeCodingVirMic-Installer-1.0.0.pkg`

首个发布版本。单文件 `.pkg` 一次装好:`VibeCodingVirMic.app` + 全部依赖(Python 运行时、numpy、sounddevice/PortAudio、rumps/PyObjC、Hush 模型与推理库)+ BlackHole 2ch 虚拟声卡驱动。

**功能:**
- 实时**去除背景人声**(Hush / DeepFilterNet3,纯 CPU,约 20 ms 延迟)。
- 菜单栏 App(显示为 **VCVMic**):Start / Stop。
- **麦克风选择**子菜单,支持**热插拔**(拔掉在用麦克风自动切系统默认;插入新麦克风只刷新列表、保持当前)。
- **5 档降噪强度**:Off(直通)/ Gentle 20 dB / Medium 40 dB / Strong 60 dB / Aggressive 100 dB。**默认 20 dB**。
- **设置窗口**(原生,固定大小)含「降噪调教」:录一段带背景音的样本(录制带**逐秒倒计时**),逐档试听对比 —— 每档显示**梅尔频谱图**、**可拖动的 seek 进度条**、**▶播放/⏸暂停** 切换,点「用这个阈值」即应用。
- 麦克风与档位选择持久化;自定义 App / 安装包图标。

**已知限制:**
- 未公证,其他 Mac 首次打开有 Gatekeeper 提示(见上)。
- 首次启动有一次性 Gatekeeper 校验延迟,之后启动很快。

---

## 模板(后续版本在上方追加)

```
## vX.Y.Z — YYYY-MM-DD
**安装包:** VibeCodingVirMic-Installer-X.Y.Z.pkg
- 新增:…
- 修复:…
- 变更:…
```
