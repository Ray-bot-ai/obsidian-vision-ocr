#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paddle_ingest.py — 新入库 PDF 用 PaddleOCR-VL(云) 做 OCR，文本写入 Obsidian 伴生 md。

替代 Apple Vision 作为「未来新入库」的 OCR 引擎。要点：
- 逐页提交（底层 paddle-ocr-techniques 的 batch 脚本把每页拆成单页 PDF 提交，每个 job 仅 1 页），
  从设计上规避官方「单次>100页会超时/被忽略」的限制，无需手动分块。
- 接入 paddle-ocr-techniques 的「缺块检测 + 定向重 OCR + 视觉覆盖审计 + 质量报告」，
  跑完会提示哪些页可能漏识别、需人工复核（评审面板见缓存目录）。
- 结果写进 文本格式文档/INFO_<名>_PDF.md 的 %% OCR-START (PaddleOCR-VL) %% 区块。
- 注意：与 Vision 不同，PaddleOCR 返回的是文本/markdown，不回写 PDF 文本层；
  可检索文本在伴生 md（会同步 iCloud，供 vault-fulltext-search 使用）。

用法：
  扫描哪些新 PDF 还没文本（伴生 md 无转写/OCR 块）：
    ~/.ocr-vision-venv/bin/python ~/ocr-tools/paddle_ingest.py scan
  处理（全部未处理 / 单个 / 强制重做）：
    ~/.ocr-vision-venv/bin/python ~/ocr-tools/paddle_ingest.py run
    ~/.ocr-vision-venv/bin/python ~/ocr-tools/paddle_ingest.py run --file "原始文档.nosync/史料/某.pdf"
    ~/.ocr-vision-venv/bin/python ~/ocr-tools/paddle_ingest.py run --force --workers 4
"""
import os, sys, glob, re, json, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 便于 import vision_ocr

HOME = os.path.expanduser("~")
VENV_PY = os.path.join(HOME, ".ocr-vision-venv", "bin", "python")
REPO = os.path.join(HOME, "ocr-tools", "paddle-ocr-techniques")
BATCH = os.path.join(REPO, "scripts", "paddle_vl_batch_ocr.py")
AUDIT = os.path.join(REPO, "scripts", "visual_ocr_audit.py")
REBUILD = os.path.join(REPO, "scripts", "rebuild_ocr_reports.py")

DEFAULT_VAULT = os.path.join(
    HOME, "Documents/YourObsidianVault")
# 扫整个 原始文档.nosync（含 史料/ 与 已有研究/ 两个子文件夹），递归
PDF_ROOT_SUBDIR = "原始文档.nosync"
MD_SUBDIR = "文本格式文档"

OCR_START = "%% OCR-START (PaddleOCR-VL，paddle_ingest.py 自动生成，重跑覆盖本块) %%"
OCR_END = "%% OCR-END %%"


def companion_md(vault, stem):
    return os.path.join(vault, MD_SUBDIR, f"INFO_{stem}_PDF.md")


def has_text(md_path):
    if not os.path.exists(md_path):
        return False
    c = open(md_path, encoding="utf-8").read()
    return ("TRANSCRIPT-START" in c) or ("OCR-START" in c)


def find_pdfs(vault, single):
    if single:
        p = single if os.path.isabs(single) else os.path.join(vault, single)
        return [p]
    root = os.path.join(vault, PDF_ROOT_SUBDIR)
    return sorted(glob.glob(os.path.join(root, "**", "*.pdf"), recursive=True))


def merged_text(out_dir):
    merged = os.path.join(out_dir, "merged.md")
    if not os.path.exists(merged):  # rebuild 没产出就自己拼最终页
        pages = sorted(glob.glob(os.path.join(out_dir, "markdown_final_v2", "page_*.md")))
        text = "\n\n".join(open(p, encoding="utf-8").read().strip() for p in pages)
        if text.strip():
            open(merged, "w", encoding="utf-8").write(text)
    return open(merged, encoding="utf-8").read().strip() if os.path.exists(merged) else ""


def flagged_pages(out_dir):
    fl = set()
    for name in ["manual_review_list_v2.md", "visual_coverage_audit_v2.md"]:
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            for m in re.findall(r"page[_ ]?0*(\d+)", open(p, encoding="utf-8").read()):
                fl.add(int(m))
    return sorted(fl)


def write_companion(vault, pdf, stem, text):
    md = companion_md(vault, stem)
    rel = os.path.relpath(pdf, vault)
    block = f"{OCR_START}\n\n## 全文（PaddleOCR-VL）\n\n{text}\n\n{OCR_END}\n"
    if os.path.exists(md):
        c = open(md, encoding="utf-8").read()
        if OCR_START in c and OCR_END in c:
            pre = c[:c.index(OCR_START)]
            post = c[c.index(OCR_END) + len(OCR_END):]
            new = pre.rstrip() + "\n\n" + block + post.lstrip()
        else:
            new = c.rstrip() + "\n\n" + block
    else:
        new = f"![[{rel}]]\nLINK: [[{rel}]]\nFILE TYPE: PDF\n\n" + block
    os.makedirs(os.path.dirname(md), exist_ok=True)
    open(md, "w", encoding="utf-8").write(new)
    return md


def add_vision_layer(pdf, lang):
    """用 Apple Vision 给该 PDF 原地补一层透明可检索文本层（不动伴生 md）。"""
    import vision_ocr
    langs = vision_ocr.LANG_PRESETS.get(lang, vision_ocr.LANG_PRESETS["zh"])
    font = vision_ocr.LAYER_FONT.get(lang, "china-s")
    vision_ocr.ocr_pdf(pdf, langs, dpi=vision_ocr.DPI, font=font, verbose=False)


def error_pages(out):
    """读 quality_v2 找 status==error 的页（提交成功但处理超时/失败的漏识别页）。"""
    errs = []
    for q in glob.glob(os.path.join(out, "quality_v2", "page_*.json")):
        try:
            d = json.load(open(q, encoding="utf-8"))
            if d.get("status") == "error":
                errs.append(d.get("page"))
        except Exception:
            pass
    return sorted(e for e in errs if e is not None)


def run_one(vault, cache, pdf, workers, force, vision_layer, lang,
            backfill_rounds=2, backfill_workers=8):
    stem = os.path.splitext(os.path.basename(pdf))[0]
    out = os.path.join(cache, stem)
    cmd = [VENV_PY, BATCH, pdf, "--output", out, "--workers", str(workers)]
    if force:
        cmd.append("--force")
    subprocess.run(cmd, check=True)                                  # 1) 逐页OCR(含缺块检测)
    # 1b) 缺页自动 backfill：报错页(提交成功但处理超时)降并发重试，服务端不忙时基本能补回
    prev = None
    for r in range(backfill_rounds):
        errs = error_pages(out)
        if not errs or (prev is not None and len(errs) >= prev):
            break  # 没缺页 或 没改善 就停
        print(f"    ↻ backfill 第{r + 1}轮：{len(errs)} 缺页，用 {backfill_workers} 并发重试", flush=True)
        subprocess.run([VENV_PY, BATCH, pdf, "--output", out,
                        "--workers", str(backfill_workers)], check=False)  # resume 只重试缺页
        prev = len(errs)
    subprocess.run([VENV_PY, AUDIT, pdf, "--ocr-root", out], check=False)   # 2) 视觉审计
    subprocess.run([VENV_PY, REBUILD, "--ocr-root", out,
                    "--merged-name", "merged.md"], check=False)       # 3) 合并+报告
    text = merged_text(out)
    md = write_companion(vault, pdf, stem, text)                      # 4) 写伴生md
    layered = False
    if vision_layer:                                                 # 5) Vision 补PDF文本层
        try:
            add_vision_layer(pdf, lang)
            layered = True
        except Exception as e:
            print(f"    （PDF文本层跳过：{e}）")
    return len(text), flagged_pages(out), md, out, layered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "run"])
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--cache", default=os.path.join(HOME, "ocr-tools", ".paddle-cache"))
    ap.add_argument("--file", default=None, help="只处理单个 PDF(相对库根或绝对路径)")
    ap.add_argument("--workers", type=int, default=16)  # 实测16并发≈7页/分(基线0.67的10倍);队列满会自动退避重试
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-vision-layer", action="store_true",
                    help="跳过用 Apple Vision 给 PDF 补可检索文本层")
    ap.add_argument("--lang", default="zh", help="Vision 文本层语言预设 zh/ja/zh+ja/en")
    ap.add_argument("--backfill-rounds", type=int, default=2,
                    help="缺页(报错页)自动重试轮数；0=关闭")
    ap.add_argument("--backfill-workers", type=int, default=8,
                    help="backfill 用的并发(调低减少服务端拥塞超时)")
    args = ap.parse_args()

    pdfs = find_pdfs(args.vault, args.file)
    todo = [p for p in pdfs if args.force or args.file or not has_text(companion_md(
        args.vault, os.path.splitext(os.path.basename(p))[0]))]

    if args.cmd == "scan":
        print(f"PDF 共 {len(pdfs)}；伴生 md 还没文本(待 OCR) {len(todo)}：")
        for p in todo:
            print("  待OCR  " + os.path.relpath(p, args.vault))
        return

    print(f"待处理 {len(todo)} 个 PDF（PaddleOCR-VL，workers={args.workers}）\n")
    for i, p in enumerate(todo, 1):
        rel = os.path.relpath(p, args.vault)
        print(f"[{i}/{len(todo)}] {rel}", flush=True)
        try:
            n, flagged, md, out, layered = run_one(
                args.vault, args.cache, p, args.workers, args.force,
                not args.no_vision_layer, args.lang,
                args.backfill_rounds, args.backfill_workers)
            errs = error_pages(out)
            gap_tip = f"；⚠ 仍缺 {len(errs)} 页(可再 backfill)" if errs else ""
            layer_tip = "，PDF已补可检索文本层" if layered else ""
            print(f"    ✓ 写入 {os.path.relpath(md, args.vault)}（{n} 字）{layer_tip}{gap_tip}")
        except subprocess.CalledProcessError as e:
            print(f"    ✗ OCR 失败：{e}")
        except Exception as e:
            print(f"    ✗ 失败：{e}")


if __name__ == "__main__":
    main()
