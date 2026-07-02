'use strict';
const { Plugin, Notice } = require('obsidian');
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');

const PY = path.join(os.homedir(), '.ocr-vision-venv', 'bin', 'python');
const VISION = path.join(os.homedir(), 'ocr-tools', 'vision_ocr.py');
const PADDLE = path.join(os.homedir(), 'ocr-tools', 'paddle_ingest.py');
const DISPATCH = path.join(os.homedir(), 'ocr-tools', 'ocr_dispatch.py');
const START = path.join(os.homedir(), 'ocr-tools', 'ocr-start');
const STOP = path.join(os.homedir(), 'ocr-tools', 'ocr-stop');

module.exports = class VisionOcrPlugin extends Plugin {
  async onload() {
    // 左侧栏按钮：脱离式启动双引擎调度（setsid+看门狗，关Obsidian/被kill都能续跑）
    this.addRibbonIcon('scan-text', 'OCR：后台启动双引擎处理（可关Obsidian）',
      () => this.launchDetached(START, 'OCR 已后台启动（双引擎+看门狗，可关Obsidian）。进度见 ~/ocr-tools/dispatch.log'));
    this.addCommand({
      id: 'dispatch-start',
      name: 'OCR 双引擎：后台启动处理所有待OCR的PDF（脱离会话）',
      callback: () => this.launchDetached(START, 'OCR 已后台启动（双引擎+看门狗）'),
    });
    this.addCommand({
      id: 'dispatch-stop',
      name: 'OCR：停止后台处理',
      callback: () => this.launchDetached(STOP, 'OCR 已请求停止'),
    });
    this.addCommand({
      id: 'dispatch-scan',
      name: 'OCR 双引擎：只看分流方案（不处理）',
      callback: () => this.run(DISPATCH, ['scan']),
    });
    // —— PaddleOCR（云，新入库默认）——
    this.addCommand({
      id: 'paddle-run',
      name: 'PaddleOCR-VL：处理新入库PDF（云）',
      callback: () => this.run(PADDLE, ['run']),
    });
    this.addCommand({
      id: 'paddle-run-fast',
      name: 'PaddleOCR-VL：快速模式（高并发16·不补PDF文本层）',
      callback: () => this.run(PADDLE, ['run', '--workers', '16', '--no-vision-layer']),
    });
    this.addCommand({
      id: 'paddle-scan',
      name: 'PaddleOCR-VL：扫描未处理的PDF（不改文件）',
      callback: () => this.run(PADDLE, ['scan']),
    });
    // —— Apple Vision（本地，备选）——
    this.addCommand({
      id: 'vision-run',
      name: 'Apple Vision：处理未OCR的PDF（本地）',
      callback: () => this.run(VISION, ['run']),
    });
    this.addCommand({
      id: 'vision-run-ja',
      name: 'Apple Vision：处理未OCR的PDF（日文）',
      callback: () => this.run(VISION, ['run', '--lang', 'ja']),
    });
    this.addCommand({
      id: 'vision-scan',
      name: 'Apple Vision：扫描未OCR的PDF（不改文件）',
      callback: () => this.run(VISION, ['scan']),
    });
  }

  // 脱离式启动 shell 脚本（ocr-start/ocr-stop）：fire-and-forget，进程独立于 Obsidian
  launchDetached(scriptPath, label) {
    try {
      const proc = spawn('/bin/bash', [scriptPath], { detached: true, stdio: 'ignore' });
      proc.unref();
      new Notice(label, 7000);
    } catch (e) {
      new Notice('启动失败：' + e.message, 8000);
    }
  }

  run(script, args) {
    if (this.running) { new Notice('Vision OCR 已在运行中…'); return; }
    this.running = true;
    const notice = new Notice('Vision OCR 启动中…', 0);
    let proc;
    try {
      proc = spawn(PY, [script, ...args]);
    } catch (e) {
      this.running = false; notice.hide();
      new Notice('启动失败：' + e.message, 8000); return;
    }
    const onData = (buf) => {
      const lines = buf.toString().split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length) notice.setMessage('Vision OCR\n' + lines[lines.length - 1].slice(0, 80));
    };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);
    proc.on('close', (code) => {
      this.running = false; notice.hide();
      new Notice(code === 0 ? 'Vision OCR 完成 ✓' : ('Vision OCR 失败（退出码 ' + code + '）'), 8000);
    });
    proc.on('error', (e) => {
      this.running = false; notice.hide();
      new Notice('启动失败：' + e.message, 8000);
    });
  }

  onunload() {}
};
