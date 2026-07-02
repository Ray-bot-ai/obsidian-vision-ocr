# Vision OCR（OCR 双引擎智能分流）

Obsidian 插件：给库内未处理的 PDF 做 OCR，加可检索文本层并回填伴生 md。

## 怎么分流（根据 OCR 情况，而非预设）
不预先猜哪个引擎好，而是**先用本地 Apple Vision 抽样试跑每本 PDF 的几页**，用两个可测信号给结果打分：

- **密度 = 读出的字数 ÷ 墨量**：墨多字少 → Vision 漏识别（烂扫描 / 竖排读不出）。
- **正常字占比**：偏低 → 乱码多。

据此决定：

- Vision 抽样**读得好**（字够、乱码少）→ 就用 **本地 Apple Vision**（免费、离线、快，顺带给 PDF 补可检索文本层）。
- Vision **读不好**（墨多字少 或 乱码多，多为竖排/烂扫件）→ 升级到 **云端 PaddleOCR-VL**（此类件 Vision 读不出，故不补文本层）。
- triage 判不了 → 保守走云 VL。

分流后，**本地 Vision 与云 VL 两条流水线并行推进**（本地 CPU vs 云 API，互不抢资源）。支持后台脱离式启动（看门狗续跑、可关 Obsidian）、`scan` 只看分流方案不处理，以及两引擎各自单独运行/扫描。

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
