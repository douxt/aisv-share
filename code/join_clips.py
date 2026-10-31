#!/usr/bin/env python3
"""join_clips.py — 多镜 xfade 拼接统一入口（2026-09-02 配置纪律机制化）。

事故：25 镜全片拼接 offset 公式错误两版（i*d-xf 截断 20s / 假设恒定 10.125 截断
27s）→ 正确 = 逐镜实测时长累积：acc += d_i - xf，offset = acc - xf。
本脚本固化该逻辑，参数从 spec 读（seam_xfade_sec）。

用法:
  python diag/join_clips.py <clip1.mp4> <clip2.mp4> ... --out <path>
  或: python diag/join_clips.py --auto --out <path>   # 自动扫 25 镜全片序列

输出: xfade 拼接 mp4（xfade/acrossfade = spec.seam_xfade_sec）
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


# 全片 25 镜序列（源路径相对 H3 output/magic）
FULL_FILM = [
    "act_1/shot_01/ref2v_384p10_run10702_00001_.mp4",
    "act_1/shot_battle_b1/ref2v_384p10_run10904_00001_.mp4",
    "act_1/shot_battle_b2/ref2v_384p10_run11001_00001_.mp4",
    "act_2/act2_2a/ref2v_384p10_run11304_00001_.mp4",
    "act_2/act2_2b/ref2v_384p10_run11307_00001_.mp4",
    "act_2/act2_2c/ref2v_384p10_run11323_00001_.mp4",
    "act_3/act3_3a/ref2v_384p10_run11403_00001_.mp4",
    "act_3/act3_3b/ref2v_384p10_run11410_00001_.mp4",
    "act_3/act3_3c/ref2v_384p10_run11421_00001_.mp4",
    "act_4/act4_4a/ref2v_384p10_run11501_00001_.mp4",
    "act_4/act4_4b/ref2v_384p10_run11520_00001_.mp4",
    "act_4/act4_4c/ref2v_384p10_run11531_00001_.mp4",
    "act_4/act4_4d/ref2v_384p10_run11541_00001_.mp4",
    "act_4/act4_4e/ref2v_384p10_run11553_00001_.mp4",
    "act_5/act5_5a1/ref2v_384p10_run11611_00001_.mp4",
    "act_5/act5_5a2/ref2v_384p10_run11622_00001_.mp4",
    "act_5/act5_5b1/ref2v_384p10_run11631_00001_.mp4",
    "act_5/act5_5b2/ref2v_384p10_run11642_00001_.mp4",
    "act_5/act5_5c1/ref2v_384p10_run11662_00001_.mp4",
    "act_5/act5_5c2/ref2v_384p10_run11672_00001_.mp4",
    "act_5/act5_5c3/ref2v_384p10_run11682_00001_.mp4",
    "act_6/act6_6a1/ref2v_384p10_run11693_00001_.mp4",
    "act_6/act6_6a2/ref2v_384p10_run11700_00001_.mp4",
    "act_6/act6_6b1/ref2v_384p10_run11711_00001_.mp4",
    "act_6/act6_6b2/ref2v_384p10_run11720_00001_.mp4",
]


def duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return float(r.stdout.strip())


def join(clips: list[str], out: str, xf: float) -> float:
    """多镜 xfade 拼接。返回总时长。offset = 累积实测时长 - xf（2026-09-02 公式固化）。"""
    n = len(clips)
    if n < 2:
        raise ValueError("至少 2 个片段")
    durs = [duration(c) for c in clips]
    fc = []
    acc = durs[0]
    for i in range(1, n):
        off = acc - xf
        if off <= 0:
            raise ValueError(f"offset {off:.3f} <= 0 at clip {i}")
        if i == 1:
            fc.append(f"[0:v][1:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[v01]")
            fc.append(f"[0:a][1:a]acrossfade=d={xf}[a01]")
        elif i < n - 1:
            fc.append(f"[v{i-1:02d}][{i}:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[v{i:02d}]")
            fc.append(f"[a{i-1:02d}][{i}:a]acrossfade=d={xf}[a{i:02d}]")
        else:
            fc.append(f"[v{i-1:02d}][{i}:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[v]")
            fc.append(f"[a{i-1:02d}][{i}:a]acrossfade=d={xf}[a]")
        acc += durs[i] - xf
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k", out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-400:]}")
    return acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="*", help="片段 mp4（缺省需 --auto）")
    ap.add_argument("--auto", action="store_true", help="自动拼全片 25 镜序列")
    ap.add_argument("--out", required=True, help="输出 mp4")
    args = ap.parse_args()

    s = spec()
    xf = float(s["seam_xfade_sec"])
    if args.auto:
        h3 = H3_ROOT.replace("\\", "/")
        clips = [os.path.join(h3, "ComfyUI", "output", SCENE, rel) for rel in FULL_FILM]
    else:
        clips = args.clips
    missing = [c for c in clips if not os.path.exists(c)]
    if missing:
        print(f"[join_clips] ✗ 缺失 {len(missing)}: {missing[0]}")
        sys.exit(1)
    total = join(clips, args.out, xf)
    print(f"[join_clips] ✓ {os.path.basename(args.out)} — {len(clips)} 镜, "
          f"{total:.1f}s, xfade={xf}s（spec）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
