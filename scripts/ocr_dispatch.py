#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_dispatch.py — 双引擎并行调度器。
对所有「待OCR」的 PDF 跑 triage 判据：横排/清晰 → 本地 Apple Vision，竖排/烂扫 → 云端 PaddleOCR-VL。
两条流水线**并行**推进（本地 CPU vs 云 API，互不抢资源），合并吞吐远超单跑。

  scan : 只列出待OCR的书及其分流(不处理)
  run  : 并行处理(默认)

本地 Vision 走 vision_ocr.py(顺带补 PDF 文本层)；云 VL 走 paddle_ingest.py --no-vision-layer
(竖排件 Vision 读不出,补层无意义)。不同书→不同文件,两条线无冲突。
"""
import os, sys, glob, time, argparse, threading, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_triage as T

HOME = os.path.expanduser("~")
VENV_PY = os.path.join(HOME, ".ocr-vision-venv", "bin", "python")
VISION = os.path.join(HOME, "ocr-tools", "vision_ocr.py")
PADDLE = os.path.join(HOME, "ocr-tools", "paddle_ingest.py")
LOCK = os.path.join(HOME, "ocr-tools", ".dispatch.lock")

VAULT = os.path.join(HOME, "Documents/YourObsidianVault")
SUBDIRS = ["原始文档.nosync/史料", "原始文档.nosync/已有研究"]
MD_DIR = os.path.join(VAULT, "文本格式文档")


def has_text(stem):
    p = os.path.join(MD_DIR, f"INFO_{stem}_PDF.md")
    if not os.path.exists(p):
        return False
    c = open(p, encoding="utf-8", errors="replace").read()
    return "OCR-START" in c or "TRANSCRIPT-START" in c


def pending():
    out = []
    for sub in SUBDIRS:
        for p in sorted(glob.glob(os.path.join(VAULT, sub, "*.pdf"))):
            stem = os.path.splitext(os.path.basename(p))[0]
            if not has_text(stem):
                out.append(p)
    return out


def route(path):
    try:
        engine, reasons = T.decide(T.probe(path, n=6))
        return ("vl" if "VL" in engine else "vision"), reasons
    except Exception as e:
        # 判不了就保守走云 VL(质量更稳)
        return "vl", [f"triage出错保守走VL:{e}"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def worker(name, books, cmd_for):
    for i, p in enumerate(books, 1):
        stem = os.path.basename(p)
        log(f"[{name}] ({i}/{len(books)}) 开始 {stem[:50]}")
        try:
            subprocess.run(cmd_for(p), check=False)
            log(f"[{name}] ({i}/{len(books)}) 完成 {stem[:50]}")
        except Exception as e:
            log(f"[{name}] ({i}/{len(books)}) 失败 {stem[:50]}: {e}")
    log(f"[{name}] 全部完成（{len(books)} 本）")


def vision_cmd(p):
    return [VENV_PY, VISION, "run", "--file", p]


def vl_cmd(p):
    return [VENV_PY, PADDLE, "run", "--file", p, "--no-vision-layer"]


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (ValueError, PermissionError):
        return pid > 0  # 权限错=进程存在
    return True


def acquire_lock():
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read().strip())
        except Exception:
            pid = -1
        if pid > 0 and pid != os.getpid() and _pid_alive(pid):
            return False  # 真有活着的调度器
        os.remove(LOCK)  # 陈旧锁(进程已死/被kill)，清掉
    open(LOCK, "w").write(str(os.getpid()))
    return True


def _cleanup_lock(*_):
    try:
        if os.path.exists(LOCK) and open(LOCK).read().strip() == str(os.getpid()):
            os.remove(LOCK)
    finally:
        os._exit(143)


def main():
    import signal
    signal.signal(signal.SIGTERM, _cleanup_lock)
    signal.signal(signal.SIGINT, _cleanup_lock)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "run", "count"], nargs="?", default="run")
    args = ap.parse_args()

    if args.cmd == "count":   # 看门狗用：快速打印还剩几本待OCR(不triage)
        print(len(pending()))
        return

    pdfs = pending()
    if not pdfs:
        log("没有待OCR的 PDF。")
        return
    log(f"待OCR {len(pdfs)} 本，正在 triage 分流…")
    vision_books, vl_books = [], []
    for p in pdfs:
        eng, reasons = route(p)
        (vl_books if eng == "vl" else vision_books).append(p)
        tag = "☁️VL " if eng == "vl" else "💻Vision"
        log(f"  {tag} {os.path.basename(p)[:50]}" + (f"  ← {';'.join(reasons)}" if reasons else ""))
    log(f"分流结果：本地Vision {len(vision_books)} 本，云VL {len(vl_books)} 本")

    if args.cmd == "scan":
        return

    # 互斥锁：避免重复启动；锁里的 PID 已死(如上次被kill)则自动清理，不再卡住
    if not acquire_lock():
        log(f"⚠ 已有调度器在运行（锁 {LOCK} 的进程仍存活）。要强制，删掉该文件再跑。")
        return
    try:
        log("两条流水线并行启动…（进度可用 ~/ocr-tools/ocr-progress 查看）")
        t_vis = threading.Thread(target=worker, args=("Vision", vision_books, vision_cmd))
        t_vl = threading.Thread(target=worker, args=("VL", vl_books, vl_cmd))
        t_vis.start(); t_vl.start()
        t_vis.join(); t_vl.join()
        log("✅ 全部完成。")
    finally:
        if os.path.exists(LOCK):
            os.remove(LOCK)


if __name__ == "__main__":
    main()
