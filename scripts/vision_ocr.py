#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_ocr.py — 用 Apple Vision 给库里的 PDF 批量加「可检索文本层」，
并把 OCR 文本写进 Obsidian 的 Binary File Manager 伴生 md。

设计要点
--------
1. 引擎：macOS 原生 Vision（VNRecognizeTextRequest，accurate 级、开语言纠错），
   对清晰印刷体中/日文质量远好于 Tesseract。
2. 原 PDF「原地写回」：在原页面图像上叠加一层透明文字（render_mode=3，看不见，
   但能选中/检索），文件名不变。写到临时文件后原子替换，中途崩溃不会损坏原件。
3. md 文本做「段落重组」：Vision 逐行返回，本脚本按每行右边缘是否顶到正文右界、
   以及是否以句末标点结尾，判断「软折行(拼接)」还是「真段落(换行)」，
   避免每行最右侧都插入硬换行符——这样跨行检索词组不会被切断。
4. 只处理「还没有文本层」的 PDF（采样若干页判断），可 --force 强制重做。

用法
----
  扫描看哪些 PDF 需要 OCR（不改动任何文件）：
      python vision_ocr.py scan
  处理所有需要 OCR 的 PDF：
      python vision_ocr.py run
  只看会做什么，不真正改文件：
      python vision_ocr.py run --dry-run
  处理单个文件 / 强制重做 / 日文材料：
      python vision_ocr.py run --file "原始文档/史料/xxx.pdf"
      python vision_ocr.py run --force
      python vision_ocr.py run --lang ja

请用 venv 里的解释器运行：
      ~/.ocr-vision-venv/bin/python ~/ocr-tools/vision_ocr.py run
"""

import argparse
import os
import sys
import tempfile
import unicodedata

import fitz  # PyMuPDF
import Vision
import Quartz
from Foundation import NSURL

# ---------------------------------------------------------------------------
# 配置默认值
# ---------------------------------------------------------------------------
DEFAULT_VAULT = os.path.expanduser(
    "~/Documents/YourObsidianVault"
)
SCAN_SUBDIR = "原始文档.nosync"   # 默认只扫这个子目录下的 PDF（.nosync 不上传 iCloud）
COMPANION_DIR = "文本格式文档"     # BFM 伴生 md 所在目录
COMPANION_FMT = "INFO_{stem}_PDF.md"  # BFM filenameFormat: INFO_{{NAME}}_{{EXTENSION:UP}}

DPI = 300                          # 渲染分辨率，小字号材料可调高到 400
NEEDS_OCR_SAMPLE_PAGES = 6         # 采样判断是否已有文本层
NEEDS_OCR_MIN_CHARS = 40           # 平均每页文本少于此值 => 视作需要 OCR

OCR_START = "%% OCR-START (vision_ocr.py 自动生成，重跑会覆盖本区块) %%"
OCR_END = "%% OCR-END %%"

LANG_PRESETS = {
    "zh": ["zh-Hans", "zh-Hant", "en-US"],
    "ja": ["ja-JP", "zh-Hant", "en-US"],
    "zh+ja": ["zh-Hans", "zh-Hant", "ja-JP", "en-US"],
    "en": ["en-US"],
}

# 隐藏文本层用的内置 CID 字体：要覆盖对应文字，检索/提取才能拿到正确 Unicode
LAYER_FONT = {
    "zh": "china-s",
    "ja": "japan-s",
    "zh+ja": "japan-s",   # japan-s 覆盖假名+汉字，兼顾中日混排
    "en": "helv",
}

TERMINAL_PUNCT = "。．.！!？?；;："  # 行尾出现这些 => 视作硬换行
# ---------------------------------------------------------------------------


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3040 <= o <= 0x30FF      # 假名
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or ch in "，。、；：！？「」『』（）《》【】…—"
    )


def ocr_image(img_path, languages):
    """对单张图片做 Vision OCR，返回 [(text, x, y, w, h), ...]（归一化坐标，原点左下）。"""
    url = NSURL.fileURLWithPath_(img_path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(languages)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = handler.performRequests_error_([req], None)
    if not ok or req.results() is None:
        return []
    out = []
    for obs in req.results():
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        bb = obs.boundingBox()
        out.append(
            (
                c.string(),
                float(bb.origin.x),
                float(bb.origin.y),
                float(bb.size.width),
                float(bb.size.height),
            )
        )
    return out


def reflow_lines(lines):
    """把 Vision 的物理行重组为段落文本：软折行拼接，真段落才换行。"""
    if not lines:
        return ""
    # 正文右界：取多数行右边缘的较大值（用 90 分位附近，避开个别超长行/页码）
    rights = sorted(x + w for _, x, _, w, _ in lines)
    right_max = rights[int(len(rights) * 0.85)] if len(rights) > 3 else max(rights)

    parts = []
    for i, (s, x, y, w, h) in enumerate(lines):
        s = s.rstrip()
        if not s:
            continue
        parts.append(s)
        if i == len(lines) - 1:
            break
        line_right = x + w
        ends_terminal = s[-1] in TERMINAL_PUNCT
        filled = line_right >= right_max - 0.03  # 顶到右界 => 软折行
        if ends_terminal or not filled:
            parts.append("\n")
        else:
            # 软折行：CJK 直接拼接，西文之间补空格
            nxt = lines[i + 1][0].lstrip()
            if s and nxt and (not is_cjk(s[-1])) and (not is_cjk(nxt[0])):
                parts.append(" ")
            # CJK 之间不加任何分隔
    text = "".join(parts)
    # 收敛多余空行
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def needs_ocr(doc):
    n = min(NEEDS_OCR_SAMPLE_PAGES, doc.page_count)
    if n == 0:
        return False
    step = max(1, doc.page_count // n)
    total = 0
    cnt = 0
    for i in range(0, doc.page_count, step):
        total += len(doc[i].get_text().strip())
        cnt += 1
        if cnt >= n:
            break
    return (total / max(cnt, 1)) < NEEDS_OCR_MIN_CHARS


# --- 文本层质检（backfill 用）---------------------------------------------
# 既有文本层的 PDF 不必重新 OCR，但要确认文本层「覆盖全书」而非
# 「只有部分页 / 只有水印或脚注」。两道闸门：
#   覆盖率  = 有实质文本的页 / 总页数        → 抓「只 OCR 了部分页」
#   非空页密度 = 非空页平均字数               → 抓「只有水印/脚注铺满每页」
BACKFILL_PAGE_MIN_CHARS = 50      # 单页 >= 此值才算「这页有文本」
BACKFILL_MIN_COVERAGE = 0.70      # 有文本的页 < 70% => 文本层不完整
BACKFILL_MIN_DENSITY = 120        # 非空页平均字数 < 此值 => 疑似仅水印/脚注


def text_layer_stats(doc):
    """逐页统计：返回 (覆盖率, 非空页平均字数, 总字数, 总页数)。"""
    n = doc.page_count
    lens = [len(doc[i].get_text().strip()) for i in range(n)]
    nonempty = [x for x in lens if x >= BACKFILL_PAGE_MIN_CHARS]
    cov = (len(nonempty) / n) if n else 0.0
    dens = (sum(nonempty) / len(nonempty)) if nonempty else 0.0
    return cov, dens, sum(lens), n


def quality_verdict(cov, dens):
    """根据覆盖率与密度判定是否可直接抽取文本层。返回 (ok, 原因)。"""
    if cov < BACKFILL_MIN_COVERAGE:
        return False, f"覆盖率仅 {cov*100:.0f}%（疑似只有部分页有文本层）"
    if dens < BACKFILL_MIN_DENSITY:
        return False, f"非空页密度仅 {dens:.0f} 字/页（疑似仅水印/脚注）"
    return True, f"覆盖率 {cov*100:.0f}%、密度 {dens:.0f} 字/页"


def extract_text_layer(doc):
    """抽取既有文本层为整本文本，按页用空行分隔，保留原有行结构。"""
    pages = []
    for i in range(doc.page_count):
        t = doc[i].get_text().strip()
        if t:
            pages.append(t)
    return "\n\n".join(pages)


def ocr_pdf(pdf_path, languages, dpi=DPI, font="china-s", verbose=True):
    """对一个 PDF 原地加文本层，返回重组后的整本文本。"""
    doc = fitz.open(pdf_path)
    all_text = []
    tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            pw, ph = page.rect.width, page.rect.height
            pix = page.get_pixmap(dpi=dpi)
            pix.save(tmp_png)
            lines = ocr_image(tmp_png, languages)
            # 叠加隐藏文本层
            for s, x, y, w, h in lines:
                if not s.strip():
                    continue
                fs = max(4.0, h * ph * 0.85)
                px = x * pw
                py = (1 - y) * ph - h * ph * 0.18  # Vision 原点左下 -> PDF 原点左上，落到基线
                try:
                    page.insert_text(
                        (px, py), s, fontname=font,
                        fontsize=fs, render_mode=3,
                    )
                except Exception:
                    # 个别字符编码失败时跳过，不影响其余
                    pass
            # 段落重组文本（按页标分隔）
            page_text = reflow_lines(lines)
            if page_text:
                all_text.append(page_text)
            if verbose and (pno + 1) % 10 == 0:
                print(f"    …{pno + 1}/{doc.page_count} 页", flush=True)
        # 原子写回
        fd, tmp_pdf = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(pdf_path))
        os.close(fd)
        doc.save(tmp_pdf, deflate=True, garbage=3)
        doc.close()
        os.replace(tmp_pdf, pdf_path)
    finally:
        if os.path.exists(tmp_png):
            os.remove(tmp_png)
    return "\n\n".join(all_text)


def companion_md_path(vault, pdf_path):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return os.path.join(vault, COMPANION_DIR, COMPANION_FMT.format(stem=stem))


def rel_to_vault(vault, pdf_path):
    return os.path.relpath(pdf_path, vault)


def write_companion(vault, pdf_path, ocr_text, dry_run=False):
    md_path = companion_md_path(vault, pdf_path)
    rel = rel_to_vault(vault, pdf_path)
    block = f"{OCR_START}\n\n## OCR 全文\n\n{ocr_text}\n\n{OCR_END}\n"

    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        if OCR_START in content and OCR_END in content:
            pre = content[: content.index(OCR_START)]
            post = content[content.index(OCR_END) + len(OCR_END):]
            new = pre.rstrip() + "\n\n" + block + post.lstrip()
        else:
            new = content.rstrip() + "\n\n" + block
    else:
        # 没有伴生 md（BFM 还没生成）就按其格式自建一个
        header = (
            f"![[{rel}]]\n"
            f"LINK: [[{rel}]]\n"
            f"FILE TYPE: PDF\n\n"
        )
        new = header + block

    if dry_run:
        return md_path, len(ocr_text)
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(new)
    return md_path, len(ocr_text)


def find_pdfs(root, single_file=None):
    if single_file:
        return [single_file]
    res = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith(".pdf"):
                res.append(os.path.join(dp, fn))
    return sorted(res)


def cmd_scan(args):
    root = args.scan_root
    pdfs = find_pdfs(root, args.file)
    need, have = [], []
    for p in pdfs:
        try:
            d = fitz.open(p)
            (need if needs_ocr(d) else have).append(p)
            d.close()
        except Exception as e:
            print(f"  [打不开] {p}: {e}")
    print(f"\n扫描根目录: {root}")
    print(f"PDF 共 {len(pdfs)} 个；已有文本层 {len(have)}；需要 OCR {len(need)}\n")
    for p in need:
        print("  需OCR  " + os.path.relpath(p, args.vault))
    return need


def cmd_run(args):
    root = args.scan_root
    pdfs = find_pdfs(root, args.file)
    languages = LANG_PRESETS.get(args.lang, LANG_PRESETS["zh"])
    font = LAYER_FONT.get(args.lang, "china-s")
    todo = []
    for p in pdfs:
        try:
            d = fitz.open(p)
            nd = args.force or needs_ocr(d)
            d.close()
            if nd:
                todo.append(p)
        except Exception as e:
            print(f"  [打不开] {p}: {e}")
    print(f"待处理 {len(todo)} 个 PDF，语言={languages}，dpi={args.dpi}，"
          f"{'(dry-run 不改文件)' if args.dry_run else ''}\n")
    for i, p in enumerate(todo, 1):
        rel = os.path.relpath(p, args.vault)
        print(f"[{i}/{len(todo)}] {rel}", flush=True)
        if args.dry_run:
            md = companion_md_path(args.vault, p)
            print(f"    -> 将写文本层并更新 {os.path.relpath(md, args.vault)}")
            continue
        try:
            text = ocr_pdf(p, languages, dpi=args.dpi, font=font)
            md, n = write_companion(args.vault, p, text)
            print(f"    ✓ 文本层已写回；伴生 md 更新 {os.path.relpath(md, args.vault)}（{n} 字）")
        except Exception as e:
            print(f"    ✗ 失败：{e}")


def cmd_backfill(args):
    """补回填：处理「PDF 已有文本层但缺伴生 md」的书。
    对每本做文本层质检（覆盖率+密度）：通过则直接抽取文本层写 md（无需 OCR）；
    不通过则只报告、不写 md，留给真 OCR（paddle/dispatch）。"""
    root = args.scan_root
    pdfs = find_pdfs(root, args.file)
    extracted, need_real_ocr, skipped_have_md = [], [], 0
    for p in pdfs:
        md = companion_md_path(args.vault, p)
        if os.path.exists(md):
            skipped_have_md += 1
            continue
        try:
            d = fitz.open(p)
            if needs_ocr(d):
                need_real_ocr.append((p, "几乎无文本层，需真 OCR"))
                d.close()
                continue
            cov, dens, total, n = text_layer_stats(d)
            ok, why = quality_verdict(cov, dens)
            if not ok:
                need_real_ocr.append((p, why))
                d.close()
                continue
            text = extract_text_layer(d)
            d.close()
            rel = os.path.relpath(p, args.vault)
            if args.dry_run:
                print(f"  [将抽取] {rel}\n           {why}，{total} 字")
                extracted.append(p)
                continue
            mdp, c = write_companion(args.vault, p, text)
            print(f"  ✓ 抽取文本层 → {os.path.relpath(mdp, args.vault)}"
                  f"（{c} 字；{why}）")
            extracted.append(p)
        except Exception as e:
            need_real_ocr.append((p, f"打开/处理出错: {e}"))
    print(f"\n回填完成：抽取写 md {len(extracted)} 本；"
          f"质检未过/需真OCR {len(need_real_ocr)} 本；已有 md 跳过 {skipped_have_md} 本")
    if need_real_ocr:
        print("\n以下书未写 md（建议走 paddle_ingest 真 OCR）：")
        for p, why in need_real_ocr:
            print(f"  ✗ {os.path.basename(p)} —— {why}")
    return extracted


def main():
    ap = argparse.ArgumentParser(description="Apple Vision 批量 OCR + 伴生 md 回填")
    ap.add_argument("cmd", choices=["scan", "run", "backfill"],
                    help="scan=只列出待OCR; run=执行OCR; "
                         "backfill=对已有文本层但缺md的书质检并抽取写md")
    ap.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian 库根目录")
    ap.add_argument("--root", default=None, help="扫描子目录（默认 库/原始文档）")
    ap.add_argument("--file", default=None, help="只处理单个 PDF（相对库根或绝对路径）")
    ap.add_argument("--lang", default="zh", choices=list(LANG_PRESETS),
                    help="识别语言预设：zh/ja/zh+ja/en")
    ap.add_argument("--dpi", type=int, default=DPI)
    ap.add_argument("--force", action="store_true", help="已有文本层也强制重做")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    args.scan_root = args.root or os.path.join(args.vault, SCAN_SUBDIR)
    if args.file and not os.path.isabs(args.file):
        args.file = os.path.join(args.vault, args.file)

    if args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "backfill":
        cmd_backfill(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
