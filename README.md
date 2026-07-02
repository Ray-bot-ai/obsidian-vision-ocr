# Vision OCR（OCR 双引擎调度）

Obsidian 插件：给库内未处理的 PDF 做 OCR，加可检索文本层并回填伴生 md。自动分流两个引擎：

- **PaddleOCR-VL（云）** —— 新入库默认，效果好。
- **Apple Vision（本地）** —— 免费、离线、快，作备选。

支持后台脱离式启动（看门狗续跑、可关 Obsidian）、只看分流方案，以及两引擎各自单独运行/扫描。

## ⚠️ 依赖外部脚本（本仓库不含）
这个插件本身只是个**启动器**，真正干活的 Python 脚本在使用者本机的 `~/ocr-tools/` 下，由插件调用：

- `~/ocr-tools/ocr_dispatch.py` —— 分流调度
- `~/ocr-tools/paddle_ingest.py` —— PaddleOCR-VL（云）
- `~/ocr-tools/vision_ocr.py` —— Apple Vision（本地）
- `~/ocr-tools/ocr-start` / `ocr-stop` —— 后台脱离式启停脚本
- Python 环境：`~/.ocr-vision-venv/`

**没有这些脚本，插件装上也跑不起来。** 这些脚本属于作者本地的 OCR 管线，未包含在此仓库中。此仓库仅公开插件外壳（`main.js` + `manifest.json`）供参考。

## 安装
把 `main.js`、`manifest.json` 拷进 `<你的库>/.obsidian/plugins/vision-ocr/`，在设置里启用。仅桌面端（macOS）。
