#!/usr/bin/env python3
"""check_lockframe_desc.py — 锁帧构图预检（md↔md 静态扫描，2026-09-03）

背景：2c 重跑 11 卡二采全废 — 2c [Shot 1] 复述 = "medium shot dou 蹲" vs 2b v4 尾段
设计 = "high-angle 全景收"（2b v3→v4 尾帧设计变更，2c 描述没跟上）。md 对 md 即可
检出 — 产物无关（全量重跑接缝唯一事实 = 当前 md 链设计）。

扫描：对每个锁帧镜（images[0] 含 tail_frame/end_frame）：
  上游 md 尾段（最后 [Shot N] 末时间戳后收尾句）framing 词
  vs 下游 md [Shot 1] 开场段（首时间戳前）framing 词
  拉开类（pulls up/high angle/wide/back away）∧ 紧凑类（medium/close-up/push in）
  = CONFLICT（反向同理）；姿态动词抽查 = warn 级。

用法:
  python diag/check_lockframe_desc.py scan          # 全链（episodes 推导 relay 关系）
  python diag/check_lockframe_desc.py pair --up <上游md> --down <下游md>  # 单对
"""
import argparse
import os
import re
import sys

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- framing 词表（软词不进表 — 只判强对立，防误报）----
WIDE = ("pulls up", "backs away", "high angle", "wide view", "wide shot",
        "overlook", "overlooking", "overhead", "top-down", "aerial",
        "full view", "wider", "pulls away")
TIGHT = ("medium shot", "close-up", "close shot", "close medium", "closeup",
         "extreme close-up", "push-in", "pushes in", "tight", "over-the-shoulder")
POSES = {"kneel": ("kneeling", "kneels", "crouched", "crouching"),
         "stand": ("standing", "stands", "upright"),
         "hold": ("holding", "holds", "carrying", "carries", "cradling"),
         "sit": ("sitting", "sits", "seated")}
SCALE_RE = re.compile(
    r"\b(close-up|close shot|close medium|medium shot|extreme close-up|"
    r"wide shot|wide view|high angle|overhead|top-down|aerial|full view)\b")


def _text_block(txt: str) -> str:
    m = re.search(r"```text\n(.*?)```", txt, re.S)
    return m.group(1) if m else ""


def downstream_rep(txt: str) -> tuple[str, list[str]]:
    """下游锁帧复述段 + 其中 framing 命中。
    模式 A：subject_definitions 的 <Picture N> = "first frame of [Shot 1], final
    frame of previous shot: …" 定义句（最可靠复述源 — 2c 案）。
    模式 B：[Shot 1] opening "opens locked on …" 至 thaws/首时间戳（4a 案）。
    尺度判定 = 复述段**首个** scale 命中（开场构图 = 首画面，后部运动词不算）。"""
    tb = _text_block(txt)
    sd = tb[: tb.find("\nsummary:")]
    m = re.search(r"<Picture \d+> is the first frame of \[Shot 1\], the final frame "
                  r"of the previous shot: (.*?)(?=\n<\w+ \d+>|\nsummary:)", sd, re.S)
    if m:
        seg = m.group(1)
        return seg[:600], framing_first(seg)
    dstart = tb.find("detailed_description:")
    m1 = re.search(r"\[Shot 1\]\s*(.*?)(?=\[Shot 2\]|\noverall_soundscape:)", tb[dstart:], re.S)
    if not m1:
        return "", []
    seg = m1.group(1)
    cut = re.search(r"\bthaws into motion\b|At \d{2}:\d{3}\.\d{3}", seg)
    if cut:
        seg = seg[: cut.start()]
    return seg[:600], framing_first(seg)


def upstream_tail(txt: str) -> tuple[str, list[str]]:
    """上游尾段 = 最后 [Shot N] 末时间戳后收尾句。尺度判定 = **末个** scale 命中
    （收尾构图 — "ends on a slow push-in" 推进收 = tight）。"""
    tb = _text_block(txt)
    dstart = tb.find("detailed_description:")
    segs = re.findall(r"\[Shot (\d+)\](.*?)(?=\[Shot \d+\]|\noverall_soundscape:)", tb[dstart:], re.S)
    if not segs:
        return "", []
    last = segs[-1][1]
    ts = list(re.finditer(r"At \d{2}:\d{3}\.\d{3}", last))
    tail = last[ts[-1].end():] if ts else last
    hits = list(SCALE_RE.finditer(tail.lower()))
    if not hits:
        return tail[:400], []
    h = hits[-1]
    return tail[max(0, h.start() - 120): h.end() + 200], [h.group(1)]


def framing_first(seg: str) -> list[str]:
    low = seg.lower()
    h = SCALE_RE.search(low)
    return [h.group(1)] if h else []


def pose_words(text: str) -> list[str]:
    low = text.lower()
    found = []
    for k, alts in POSES.items():
        if any(a in low for a in alts):
            found.append(k)
    return found


WIDE_SCALE = {"wide", "high angle", "wide shot", "wide view", "overhead", "top-down",
              "aerial", "full view"}
TIGHT_SCALE = {"close-up", "close shot", "close medium", "medium shot",
               "extreme close-up", "push-in", "pushes in", "tight",
               "over-the-shoulder"}


def judge(up_tail: str, down_open: str, up_f: list[str], down_f: list[str]) -> dict:
    """单对判定。CONFLICT = 上游 wide 向 ∧ 下游 tight 向（锁帧必败）；反向同理。"""
    def cls(fs):
        if any(w in WIDE_SCALE for w in fs):
            return "wide"
        if any(w in TIGHT_SCALE for w in fs):
            return "tight"
        return "neutral"
    uc, dc = cls(up_f), cls(down_f)
    verdict = "OK"
    if (uc == "wide" and dc == "tight") or (uc == "tight" and dc == "wide"):
        verdict = "CONFLICT"
    up_pose, down_pose = pose_words(up_tail), pose_words(down_open)
    warn = []
    if verdict == "OK" and up_pose and down_pose and not set(up_pose) & set(down_pose):
        warn.append(f"姿态不一致（上游 {up_pose} vs 下游 {down_pose} — 可能非主体）")
    return {"up_framing": up_f, "down_framing": down_f,
            "up_class": uc, "down_class": dc, "up_pose": up_pose, "down_pose": down_pose,
            "verdict": verdict, "warn": warn}


def pair_check(up_md: str, down_md: str, up_shot: str, down_shot: str) -> dict:
    up = open(os.path.join(AISV_ROOT, up_md), encoding="utf-8").read()
    down = open(os.path.join(AISV_ROOT, down_md), encoding="utf-8").read()
    up_tail, up_f = upstream_tail(up)
    down_open, down_f = downstream_rep(down)
    r = judge(up_tail, down_open, up_f, down_f)
    r["up_shot"] = up_shot
    r["down_shot"] = down_shot
    return r


def scan_all():
    sys.path.insert(0, SCRIPT_DIR)
    sys.path.insert(0, SCRIPT_DIR)
    import chain_check
    # 锁帧镜：images[0] 含 tail_frame/end_frame + md 可定位
    results = []
    for md_rel in chain_check.all_mds():
        fm = chain_check.parse_fm(os.path.join(AISV_ROOT, md_rel))
        shot = fm["shot"]
        imgs = fm.get("images") or []
        lock = any("tail_frame" in i or "end_frame" in i for i in imgs)
        if not lock:
            continue
        vr = fm.get("video_ref") or ""
        up_shot = os.path.basename(vr).replace("_tail24.mp4", "") if "_tail24" in vr else ""
        if not up_shot:
            results.append({"down_shot": shot, "md": md_rel, "up_shot": None,
                            "verdict": "SKIP", "warn": ["无上游推导（video_ref 缺/非 tail24）"]})
            continue
        up_md = chain_check.resolve_md(up_shot)
        if not up_md:
            results.append({"down_shot": shot, "md": md_rel, "up_shot": up_shot,
                            "verdict": "SKIP", "warn": [f"上游 {up_shot} md 缺失"]})
            continue
        r = pair_check(up_md, md_rel, up_shot, shot)
        r["md"] = md_rel
        results.append(r)
    return results


if __name__ == "__main__":
    import json
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="全链扫描")
    p.set_defaults(fn=lambda a: scan_all())
    p = sub.add_parser("pair", help="单对检查")
    p.add_argument("--up", required=True, help="上游 md（相对 AISV_ROOT）")
    p.add_argument("--down", required=True, help="下游 md")
    p.set_defaults(fn=lambda a: [pair_check(a.up, a.down, a.up, a.down)])
    a = ap.parse_args()
    rows = a.fn(a)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    # exit 1 = 有 CONFLICT（门禁复用）
    n_conflict = sum(1 for r in rows if r.get("verdict") == "CONFLICT")
    sys.exit(1 if n_conflict else 0)
