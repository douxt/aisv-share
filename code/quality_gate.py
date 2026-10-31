#!/usr/bin/env python3
"""quality_gate.py — 二采产物质量门禁（2026-09-02 自动管线组件，import 复用）

检查串（顺序重要 — ffprobe 分辨率先于语义检查，防语义漂移解读）:
  1. ffprobe 分辨率 == spec.deliver_resolution        （全镜）
  2. tail_check.analyze verdict == OK                  （末帧花屏检测）
  3. 锁帧镜：首帧像素差 vs 上镜尾帧 relay ref          （双界定案 2026-09-02）
     双界: <15 优 / <25 及格 / ≥50 废
     PASS = diff < 25；≥50 = 直接废（打回/标记人工）
  硬切镜跳第 3 项。

用法:
  python diag/quality_gate.py <video.mp4> [--ref tail_frame.png] [--deliver 1280x704]
退出码: 0 = PASS, 1 = FAIL, 2 = 废（≥50 或 tail FLAKY）
"""
import argparse
import json
import os
import subprocess
import sys

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
# SPEC_PATH 来自 common

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

THRESH_GOOD = 15.0   # <15 优
THRESH_PASS = 25.0   # <25 及格（PASS 线）
THRESH_TRASH = 50.0  # ≥50 废


def spec() -> dict:
    return json.load(open(SPEC_PATH, encoding="utf-8"))


def ffprobe_resolution(path: str) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        path], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def tail_verdict(path: str) -> dict:
    """tail_check.analyze（cv2 花屏检测）。"""
    sys.path.insert(0, SCRIPT_DIR)
    import tail_check
    return tail_check.analyze(path)


def first_frame_diff(video: str, ref_png: str) -> float:
    """产物首帧 vs ref 尾帧像素差（check_frames.diff 逻辑复用，同 320x176 归一）。
    产物分辨率 == ref 同源（1280x704 vs tail_frame 同规格）→ 直接 diff。"""
    sys.path.insert(0, SCRIPT_DIR)
    import check_frames
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_gate_frame_tmp.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                    "-frames:v", "1", tmp], check=True, capture_output=True, timeout=60)
    d = check_frames.diff(tmp, ref_png)
    os.remove(tmp)
    return d


def gate(video: str, ref_png: str | None = None,
         deliver: str | None = None) -> dict:
    """门禁主函数。返回 {pass, reason, checks:{...}}。
    pass: True=通过 / False=FAIL 打回；trashed: True=直接废。"""
    s = spec()
    deliver = deliver or s["deliver_resolution"]
    exp_w, exp_h = deliver.lower().split("x")
    checks = {}
    # 1. 分辨率 == spec
    try:
        w, h = ffprobe_resolution(video)
        res_ok = (str(w), str(h)) == (exp_w, exp_h)
    except Exception as e:
        return {"pass": False, "trashed": True, "reason": f"分辨率探测失败: {e}",
                "checks": {"resolution": f"probe error {e}"}}
    checks["resolution"] = f"{w}x{h} vs spec {deliver}"
    if not res_ok:
        return {"pass": False, "trashed": True,
                "reason": f"分辨率 {w}x{h} != spec {deliver}",
                "checks": checks}
    # 2. tail 花屏
    try:
        t = tail_verdict(video)
        tail_ok = t["verdict"] == "OK"
        checks["tail"] = f"{t['verdict']} last_lap={t['last_lap']:.0f} flaky_start={t['flaky_start']}/{t['frames']}"
    except Exception as e:
        return {"pass": False, "trashed": True, "reason": f"tail_check 失败: {e}",
                "checks": checks}
    if not tail_ok:
        return {"pass": False, "trashed": True,
                "reason": f"末帧花屏 {t['verdict']}", "checks": checks}
    # 3. 锁帧镜首帧 diff（硬切镜 ref=None 跳过）
    if ref_png:
        if not os.path.exists(ref_png):
            return {"pass": False, "trashed": True,
                    "reason": f"锁帧 ref 缺失: {ref_png}", "checks": checks}
        d = first_frame_diff(video, ref_png)
        band = "优" if d < THRESH_GOOD else ("及格" if d < THRESH_PASS else
               "废" if d >= THRESH_TRASH else "FAIL 打回")
        checks["first_frame_diff"] = f"{d:.1f}（{band}）"
        if d >= THRESH_TRASH:
            return {"pass": False, "trashed": True,
                    "reason": f"首帧 diff {d:.1f} ≥ 50 = 未锁住（废）", "checks": checks}
        if d >= THRESH_PASS:
            return {"pass": False, "trashed": False,
                    "reason": f"首帧 diff {d:.1f} ≥ 25（打回）", "checks": checks}
        return {"pass": True, "trashed": False,
                "reason": f"全检通过（锁帧 diff {d:.1f} = {band}）", "checks": checks}
    return {"pass": True, "trashed": False, "reason": "全检通过（硬切镜）",
            "checks": checks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="二采产物 mp4")
    ap.add_argument("--ref", default="", help="锁帧 ref png（硬切镜省略）")
    ap.add_argument("--deliver", default="", help="规格分辨率（缺省 spec）")
    args = ap.parse_args()
    r = gate(args.video, args.ref or None, args.deliver or None)
    print(f"PASS={r['pass']} trashed={r['trashed']}")
    print(f"  reason: {r['reason']}")
    for k, v in r["checks"].items():
        print(f"  {k}: {v}")
    return 0 if r["pass"] else (2 if r["trashed"] else 1)


if __name__ == "__main__":
    sys.exit(main())
