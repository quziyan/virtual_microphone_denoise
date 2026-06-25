# Windows 语音 Agent 辅助编程使用说明

本文说明如何在 Windows 11 上使用 `VibeCodingVirMic-Windows` 做降噪虚拟麦克风，并把语音输入接入 Codex CLI、Codex App 或其他网页 Agent。

## 1. 当前能力边界

`VibeCodingVirMic-Windows` 本身不是 Agent，也不做语音识别。它负责实时音频链路：

```text
真实麦克风 -> Hush/Weya NC 降噪 -> VB-CABLE 虚拟声卡 -> 目标应用麦克风
```

Codex CLI 当前不原生支持麦克风输入。要在 Codex CLI 里用语音，需要借助 Windows 语音输入把语音转成文字，再提交给 Codex。

## 2. 已配置文件

Windows 可执行文件：

```powershell
D:\caodengchan\virtual_microphone\dist\VibeCodingVirMic-Windows-1.0.5.exe
```

Windows 降噪库：

```powershell
D:\caodengchan\virtual_microphone\vendor\lib\weya_nc.dll
```

模型文件：

```powershell
D:\caodengchan\virtual_microphone\vendor\models\advanced_dfnet16k_model_best_onnx.tar.gz
```

虚拟音频线缆使用 VB-Audio Virtual Cable。目标应用里选择的麦克风应是：

```text
CABLE Output (VB-Audio Virtual Cable)
```

## 3. 启动降噪虚拟麦克风

打开 PowerShell：

```powershell
cd D:\caodengchan\virtual_microphone
.\dist\VibeCodingVirMic-Windows-1.0.5.exe
```

看到类似输出表示启动成功：

```text
VibeCodingVirMic Windows started
Input : [1] 麦克风阵列 (Senary Audio)
Output: [5] CABLE Input (VB-Audio Virtual C
Mode  : denoise 20 dB
```

黑色窗口不要关闭。它关闭后，虚拟麦克风就没有降噪音频输入。

停止时在窗口里按：

```text
Ctrl+C
```

## 4. Windows 声音设置

进入：

```text
设置 -> 系统 -> 声音 -> 输入
```

选择：

```text
CABLE Output (VB-Audio Virtual Cable)
```

对着真实麦克风说话，确认 Windows 输入音量条会动。

注意名称容易混：

```text
本程序输出到 CABLE Input
目标应用选择 CABLE Output 当麦克风
```

不要在目标应用里选择 `CABLE Input`。

## 5. Codex CLI 用法

Codex CLI 不直接读取麦克风。推荐链路是：

```text
你的声音 -> VibeCodingVirMic 降噪 -> VB-CABLE -> Windows 语音输入 -> Codex CLI 文本 prompt
```

步骤：

```powershell
cd D:\quzhi
codex
```

进入 Codex CLI 后，让光标停在输入框，按：

```text
Win + H
```

开始说话。Windows 会把语音转成文字输入到 Codex CLI。检查转写内容没问题后，按 Enter 发送。

如果 `Win + H` 在当前终端里没有把文字输入进去，可以先在记事本里语音输入，再复制到 Codex CLI。

## 6. Codex App 用法

如果想要更接近原生语音 Agent 的体验，可以启动 Codex App：

```powershell
codex app
```

在 Codex App 的输入框可见时，按住：

```text
Ctrl+M
```

开始说话。Codex App 会把语音转写成 prompt。确认文字后发送。

仍然建议把 Windows 默认输入设备设为：

```text
CABLE Output (VB-Audio Virtual Cable)
```

这样 Codex App 接收到的是降噪后的声音。

## 7. 网页 Agent 用法

ChatGPT、Claude、Cursor 网页版或其他浏览器 Agent 可以直接选择虚拟麦克风。

Chrome 设置路径：

```text
设置 -> 隐私和安全 -> 网站设置 -> 麦克风
```

默认麦克风选择：

```text
CABLE Output (VB-Audio Virtual Cable)
```

网页弹出麦克风权限时，允许访问麦克风。

## 8. 语音提示词模板

语音输入时建议按固定结构说，减少漏信息：

```text
目标：
约束：
输入路径：
输出路径：
不要做什么：
验证方式：
```

示例：

```text
目标：检查 D:\quzhi 项目里 dynamic_peak_metrics_export.py 的上一单链路字段为什么为空。
约束：先读代码和样例 Excel，不要马上改代码。
输入路径：D:\quzhi\data\beijing_50_stores_20260510_20260609_score_gt_0_empty_lt_30
输出路径：先只输出分析结论。
不要做什么：不要重跑所有门店。
验证方式：如果确认是代码问题，先重跑北京二十八店验证。
```

实现类任务可以这样说：

```text
先搜索相关文件，遵循现有代码风格，改完后跑最小验证。如果测试失败，继续修到通过。不要改无关文件。
```

## 9. 常用命令

查看音频设备：

```powershell
cd D:\caodengchan\virtual_microphone
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --list-devices
```

指定输入和输出设备：

```powershell
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --input-device 1 --output-device 5
```

降低背景保留，增强降噪：

```powershell
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --atten-lim-db 40
```

保守降噪：

```powershell
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --atten-lim-db 20
```

强制不开降噪，只做路由：

```powershell
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --passthrough
```

自检 DLL 和模型是否被打进 exe：

```powershell
.\dist\VibeCodingVirMic-Windows-1.0.5.exe --selftest
```

## 10. 排查问题

Agent 听不到声音：

- 确认 `VibeCodingVirMic-Windows-1.0.5.exe` 黑窗口还开着。
- 确认 Windows 输入设备是 `CABLE Output`。
- 确认浏览器或目标应用的麦克风也是 `CABLE Output`。
- 确认没有误选 `CABLE Input`。

启动后仍是 passthrough：

- 运行 `--selftest`。
- 确认输出里 `Windows Weya DLL: present`。
- 确认启动日志里有 `Mode  : denoise 20 dB` 或类似 `denoise` 字样。

声音很小或识别不准：

- Windows 声音设置里检查 `CABLE Output` 输入音量。
- 确认真实麦克风本身正常。
- 尝试 `--atten-lim-db 20`，避免过强降噪影响语音识别。

背景人声仍明显：

- 尝试 `--atten-lim-db 40`。
- 让真实麦克风尽量靠近说话人。
- 目标应用中不要再叠加另一个虚拟降噪麦克风，避免双重处理。

## 11. 推荐工作流

日常本地代码任务：

```text
启动 VibeCodingVirMic -> 打开 Codex CLI -> Win+H 语音输入 -> 检查文字 -> Enter 发送
```

需要更自然语音交互：

```text
启动 VibeCodingVirMic -> codex app -> Ctrl+M 语音输入 -> 检查文字 -> 发送
```

网页 Agent：

```text
启动 VibeCodingVirMic -> 浏览器麦克风选 CABLE Output -> 使用网页语音按钮
```

核心记法：

```text
程序写入 CABLE Input，应用选择 CABLE Output。
```
