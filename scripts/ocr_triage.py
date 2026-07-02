#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_triage.py — 用本地 Apple Vision 抽样打分，判断一本 PDF 该走「本地Vision」还是「云端PaddleOCR-VL」。
原则：不预先猜，用快引擎试跑 + 可测信号打分。打印每本的分数与决定，供验证/调阈值。

用法： ~/.ocr-vision-venv/bin/python ~/ocr-tools/ocr_triage.py "书1.pdf" "书2.pdf" ...
"""
import os, sys, statistics as st
import fitz, Vision, Quartz
from Foundation import NSURL
from PIL import Image
import numpy as np

LANGS = ["zh-Hans", "zh-Hant", "en-US"]
TMP = f"/tmp/_triage_{os.getpid()}.png"  # 按进程唯一，避免多个 triage 并发写同一文件互相覆盖


def vision_lines(png):
    url = NSURL.fileURLWithPath_(png)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(LANGS)
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    h.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        c = obs.topCandidates_(1)
        if c:
            out.append((c[0].string(), float(c[0].confidence())))
    return out


def ink_ratio(png):
    a = np.asarray(Image.open(png).convert("L"))
    return float((a < 128).mean())  # 暗像素占比≈墨量


NORMAL_PUNCT = set("，。、；：！？“”‘’（）《》〈〉【】「」『』·…—-,.;:!?()[]\"'/%　 ")


def cjk_frac(text):
    """「正常字符」占比：汉字+假名+字母+数字+常见标点都算正常；替换符/韩文/西里尔/方框等才算异常(乱码)。
    数字必须算正常——兵器/财政/表格类档案数字极多,否则会把干净档案误判成乱码。"""
    t = [c for c in text if not c.isspace()]
    if not t:
        return 1.0
    normal = sum(1 for c in t if (
        "一" <= c <= "鿿" or "぀" <= c <= "ヿ"          # 汉字/假名
        or (c.isascii() and c.isalnum())                  # 字母+数字
        or c.isdigit()                                    # 全角数字等
        or c in NORMAL_PUNCT))
    return normal / len(t)


def probe(path, n=8, dpi=200):
    doc = fitz.open(path)
    P = doc.page_count
    idx = sorted(set(min(P - 1, int(P * (i + 0.5) / n)) for i in range(n)))
    rows = []
    for p in idx:
        doc[p].get_pixmap(dpi=dpi).save(TMP)
        lines = vision_lines(TMP)
        text = "".join(l[0] for l in lines)
        ink = ink_ratio(TMP)
        # 按行长加权的置信度：页码/分隔符等噪声短行权重低，反映正文真实质量
        wsum = sum(len(l[0]) for l in lines)
        wconf = (sum(l[1] * len(l[0]) for l in lines) / wsum) if wsum else 0.0
        rows.append(dict(
            chars=len(text),
            wconf=wconf,
            ink=ink,
            density=(len(text) / (ink * 100) if ink > 0 else 0.0),  # 每1%墨量的字数
            normchar=cjk_frac(text),
        ))
    agg = {k: st.mean(r[k] for r in rows) for k in rows[0]}
    return agg


def decide(a):
    # 只用跨语种稳健的信号：墨多字少(漏识别/烂扫描) + 乱码。
    # 不用置信度——Vision 置信度跨语种不可比(清晰英文也常~0.5),会误升级。
    # 漏网的(自信地读错)交给「OCR后视觉审计+逐页升级」兜底。
    reasons = []
    # 密度低=Vision读出的字远少于页面墨量应有的(漏识别/烂扫描/竖排失效)。
    # ink>0.02 仅用于排除真·空白页(空白页墨量极低)，不再用 0.12 误挡浅墨竖排件。
    if a["density"] < 30 and a["ink"] > 0.02:
        reasons.append(f"墨多字少/读不出(密度{a['density']:.0f}/墨{a['ink']:.0%})")
    if a["normchar"] < 0.60:
        reasons.append(f"乱码多(正常字{a['normchar']:.0%})")
    return ("☁️ 云端VL" if reasons else "💻 本地Vision"), reasons


if __name__ == "__main__":
    print(f"{'书名':32} {'加权置信':>7} {'墨%':>5} {'密度':>5} {'正常字%':>7}  决定")
    print("-" * 92)
    for path in sys.argv[1:]:
        try:
            a = probe(path)
            d, reasons = decide(a)
            name = path.rsplit("/", 1)[-1][:30]
            print(f"{name:32} {a['wconf']:7.2f} "
                  f"{a['ink']:5.1%} {a['density']:5.0f} {a['normchar']:7.0%}  {d}"
                  + (f"  ← {'; '.join(reasons)}" if reasons else ""))
        except Exception as e:
            print(f"{path.rsplit('/',1)[-1][:30]:32} 出错: {e}")
