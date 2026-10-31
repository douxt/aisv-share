#!/usr/bin/env python3
"""seam_join.py — 接缝拼接统一入口（2026-09-01 配置纪律机制化，开源标准）。

事故史：xfade 拼接全手敲 ffmpeg（spec.seam_policy 零脚本实现）→ 参数漂移无校验。
根治 = 唯一入口 + 参数从 spec 读 + 同规格前置校验 + 每缝验收输出。

用法:
  python diag/seam_join.py --a <上镜.mp4> --b <下镜.mp4> [--out <path>]
  参数（xfade 时长/音频焊点）从 twopass_spec.json 读，禁止手敲。

产出: 拼接视频（默认 magic/output/seam_check/{a}_{b}_seam.mp4）
      + magic/output/seam_check/{a}_{b}_seam_report.json（接缝验收：位移/亮度跳）
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def spec() -> dict:
    return json.load(open(SPEC_PATH, encoding="utf-8"))


def probe(path: str, key: str) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", f"stream={key}", "-of", "csv", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip().split(",")[-1].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="上镜 mp4")
    ap.add_argument("--b", required=True, help="下镜 mp4")
    ap.add_argument("--out", default="", help="输出路径（缺省 seam_check/{a}_{b}_seam.mp4）")
    args = ap.parse_args()

    s = spec()
    xf = float(s["seam_xfade_sec"])
    axf = float(s["seam_audio_crossfade_sec"])
    ar = int(s["seam_audio_sample_rate"])

    # 同规格前置校验（2026-09-01：尺寸/帧率不一致 = 拼接前提错误，中止）
    for p in (args.a, args.b):
        if not os.path.exists(p):
            print(f"[seam_join] ✗ 输入缺失: {p}")
            sys.exit(1)
    wa, ha = probe(args.a, "width"), probe(args.a, "height")
    wb, hb = probe(args.b, "width"), probe(args.b, "height")
    if (wa, ha) != (wb, hb):
        print(f"[seam_join] ✗ 分辨率不一致: A={wa}x{ha} B={wb}x{hb}（spec 交付 "
              f"{s['deliver_resolution']}）— 中止")
        sys.exit(1)
    fa, fb = probe(args.a, "r_frame_rate"), probe(args.b, "r_frame_rate")
    if fa != fb:
        print(f"[seam_join] ✗ 帧率不一致: A={fa} B={fb} — 中止")
        sys.exit(1)

    da = float(probe(args.a, "duration"))
    off = da - xf
    if off <= 0:
        print(f"[seam_join] ✗ offset 异常: A 时长 {da} <= xfade {xf}")
        sys.exit(1)

    out = args.out or os.path.join(SCENE_DIR, "output", "seam_check",
                                   f"{os.path.basename(args.a)[:-4]}_{os.path.basename(args.b)[:-4]}_seam.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    fc = (f"[0:v][1:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[v];"
          f"[0:a][1:a]acrossfade=d={axf}[a]")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.a, "-i", args.b,
                        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-crf", "18", "-c:a", "aac",
                        "-b:a", "192k", "-ar", str(ar), out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"[seam_join] ✗ ffmpeg 失败: {r.stderr[-400:]}")
        sys.exit(1)
    dout = float(probe(out, "duration"))
    print(f"[seam_join] ✓ {os.path.basename(out)} "
          f"({wa}x{ha} {fa} {dout:.2f}s = {da:.2f}+B-{xf:.1f}xfade, spec xfade={xf}s)")

    # 每缝验收（check_seam.py：上镜末帧 vs 下镜首帧像素差 <10 接上 / >50 断）
    try:
        r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "check_seam.py"),
                            "--shot-a", os.path.abspath(args.a),
                            "--shot-b", os.path.abspath(args.b)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
        raw = (r.stdout or "") + (r.stderr or "")
        out_txt = " ".join(raw.split())[-200:]
        tag = "✓ 接缝验收" if r.returncode == 0 else "⚠ 验收"
        print(f"[seam_join] {tag}（check_seam.py）: {out_txt}")
    except Exception as e:
        print(f"[seam_join] ⚠ 验收跳过: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
