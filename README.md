# Vision OCR（OCR 双引擎调度）

Obsidian 插件：给库内未处理的 PDF 做 OCR，加可检索文本层并回填伴生 md。自动分流两个引擎：

- **PaddleOCR-VL（云）** —— 新入库默认，效果好。
- **Apple Vision（本地）** —— 免费、离线、快，作备选。

支持后台脱离式启动（看门狗续跑、可关 Obsidian）、只看分流方案，以及两引擎各自单独运行/扫描。

## 后端脚本（本仓库已附，需自填令牌）
这个插件本身只是个**启动器**，真正干活的 Python 脚本已放在 [`scripts/`](scripts/) 目录（**已删除密钥**）：

- `scripts/ocr_dispatch.py` —— 分流调度
- `scripts/paddle_ingest.py` —— PaddleOCR-VL（云）
- `scripts/vision_ocr.py` —— Apple Vision（本地）
- `scripts/ocr_triage.py` —— 分流判定（被 dispatch 依赖）
- `scripts/ocr-start` / `ocr-stop` / `ocr-progress` —— 后台脱离式启停/看进度
- `scripts/patches/` —— 对第三方库 paddle-ocr-techniques 的本地补丁

**脚本里不含任何 API Key。** PaddleOCR-VL 的访问令牌要你自己填（见 [`scripts/paddleocr.env.example`](scripts/paddleocr.env.example)：复制为 `paddleocr.env` 填入自己的令牌，或 `export PADDLEOCR_ACCESS_TOKEN=...`）。第三方库 `paddle-ocr-techniques` 未包含，需自行 clone 后打上 `scripts/patches/` 里的补丁。

完整部署步骤见 **[scripts/SETUP.md](scripts/SETUP.md)**。脚本默认放在 `~/ocr-tools/`、Python 环境 `~/.ocr-vision-venv/`；库路径与令牌两处需自改。

## 安装
把 `main.js`、`manifest.json` 拷进 `<你的库>/.obsidian/plugins/vision-ocr/`，在设置里启用。仅桌面端（macOS）。
