#!/usr/bin/env python3
"""check_frames.py — 抽卡后首帧 vs ref 报告（2026-08-25 选卡辅助）

提取目录内全部抽卡产物首帧 → 像素差 vs ref → 表。
锁帧判定：<10 = 锁住（首帧 = ref 内容），>50 = 未锁/污染。

用法:
  python diag/check_frames.py --dir <产物目录> --ref <ref png> [--prefix fl2v11_draw_480p15]
"""
import argparse
import glob
import os
import subprocess
import sys

from PIL import Image


def diff(a: str, b: str) -> float:
    ia = Image.open(a).convert("RGB").resize((320, 176))
    ib = Image.open(b).convert("RGB").resize((320, 176))
    pa, pb = ia.load(), ib.load()
    return sum(abs(pa[x, y][0] - pb[x, y][0]) + abs(pa[x, y][1] - pb[x, y][1]) +
               abs(pa[x, y][2] - pb[x, y][2]) for x in range(320) for y in range(176)) \
        / (320 * 176 * 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="产物目录")
    ap.add_argument("--ref", required=True, help="ref 图路径")
    ap.add_argument("--prefix", default="fl2v11_draw_480p15", help="产物前缀过滤")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, f"{args.prefix}_run*.mp4")))
    if not files:
        print(f"[error] 无匹配产物: {args.dir}/{args.prefix}_run*")
        sys.exit(1)

    print(f"{'run':<10} {'像素差':<8} 判定")
    print("-" * 32)
    for f in files:
        name = os.path.basename(f)
        run = name.split("run")[1].split("_")[0]
        tmp = "_frame_tmp.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f,
                        "-frames:v", "1", tmp], check=True, capture_output=True)
        d = diff(tmp, args.ref)
        os.remove(tmp)
        verdict = "锁住" if d < 10 else ("中等" if d < 50 else "未锁/污染")
        print(f"{run:<10} {d:<8.1f} {verdict}")


if __name__ == "__main__":
    main()
