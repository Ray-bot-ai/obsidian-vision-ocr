# 部署说明：Vision OCR 双引擎的后端脚本

这些是 `vision-ocr` 插件在本机调用的脚本。插件本身只是启动器，真正干活的是这里的脚本。
**脚本里不含任何密钥**——PaddleOCR-VL 的访问令牌需要你自己提供（见下）。

## 分流原理（根据 OCR 情况，而非预设）
`ocr_triage.py` 对每本 PDF **先用本地 Apple Vision 抽样试跑几页**，算「密度=字数÷墨量」和「正常字占比」两个信号：墨多字少（漏识别/竖排读不出）或乱码多 → 判为难件走**云端 PaddleOCR-VL**；否则留**本地 Apple Vision**（省钱离线、顺带补 PDF 文本层）。`ocr_dispatch.py` 据此把待处理 PDF 分成两队，两条流水线并行跑。

## 文件一览
- `ocr_dispatch.py` —— 双引擎调度：按 triage 结果把每本 PDF 分给本地/云，两条流水线并行。
- `paddle_ingest.py` —— PaddleOCR-VL（云）入库，逐页提交。
- `vision_ocr.py` —— Apple Vision（本地，macOS）。
- `ocr_triage.py` —— 分流判定（本地 Vision 抽样打分；被 `ocr_dispatch.py` 依赖）。
- `ocr-start` / `ocr-stop` / `ocr-progress` —— 后台脱离式启动 / 停止 / 看进度。
- `patches/paddle-ocr-techniques.local.diff` —— 对第三方库打的本地补丁（重试/退避/分页等）。

## 依赖
1. **Python 环境**（脚本默认 `~/.ocr-vision-venv/bin/python`）：
   ```bash
   python3 -m venv ~/.ocr-vision-venv
   ~/.ocr-vision-venv/bin/pip install requests pyobjc-framework-Vision pyobjc-framework-Quartz
   ```
   （`pyobjc-framework-Vision` 仅 macOS；Apple Vision 引擎依赖它。）
2. **第三方库 paddle-ocr-techniques**（PaddleOCR-VL 的批处理封装，未包含在本仓库）：
   ```bash
   cd ~/ocr-tools
   git clone https://github.com/anon-research-tools/paddle-ocr-techniques
   cd paddle-ocr-techniques
   # 打上本仓库带的本地补丁（重试/退避/分页等）；把路径换成你放 scripts/ 的位置
   git apply /path/to/scripts/patches/paddle-ocr-techniques.local.diff
   ```
   补丁基于该库某个提交，若冲突需手动对齐。

## 放置位置
脚本默认放在 `~/ocr-tools/` 下（`ocr-start` 等按此路径查找）。把本目录内容拷到 `~/ocr-tools/` 即可。

## 两处要你自己改/填
1. **你的 Obsidian 库路径**：`ocr_dispatch.py`、`paddle_ingest.py`、`vision_ocr.py` 里默认写的是
   `~/Documents/YourObsidianVault`，改成你自己的库路径。
2. **PaddleOCR-VL 访问令牌**：复制 `paddleocr.env.example` 为 `paddleocr.env` 填入你自己的令牌，
   放到 `paddle-ocr-techniques/.local/paddleocr.env`；或 `export PADDLEOCR_ACCESS_TOKEN=你的令牌`。
   令牌从百度 AI Studio / PaddleX 星河社区自取。

## 跑起来
```bash
~/ocr-tools/ocr-start      # 后台双引擎启动（可关 Obsidian）
~/ocr-tools/ocr-progress   # 看进度
~/ocr-tools/ocr-stop       # 停止
```
或直接用 Obsidian 里 `vision-ocr` 插件的命令 / 左侧栏按钮触发。

> 注：这些脚本是作者本地 OCR 管线的一部分，按原样公开供参考，不保证在你的环境即插即用。
