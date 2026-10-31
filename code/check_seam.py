#!/usr/bin/env python3
"""check_seam.py — 两镜接缝像素差（2026-08-25 工具化）

下镜首帧 vs 上镜末帧平均像素差（resize 320×176）：<10 = 接上（同源），>50 = 断。

用法:
  python diag/check_seam.py --shot-a <上镜产物A.mp4> --shot-b <下镜产物B.mp4>
  python diag/check_seam.py --shot-b <下镜产物B.mp4> --ref <ref png>   # 二采首帧 vs ref 文件
"""
import argparse
import subprocess
import sys

from PIL import Image


def extract(video_or_img: str, dst: str, tail: bool = False) -> None:
    if video_or_img.lower().endswith((".png", ".jpg", ".jpeg")):
        from shutil import copyfile
        copyfile(video_or_img, dst)
    else:
        sseof = ["-sseof", "-0.05"] if tail else []
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + sseof +
                       ["-i", video_or_img, "-frames:v", "1", dst],
                       check=True, capture_output=True)


def diff(a: str, b: str) -> float:
    ia = Image.open(a).convert("RGB").resize((320, 176))
    ib = Image.open(b).convert("RGB").resize((320, 176))
    pa, pb = ia.load(), ib.load()
    return sum(abs(pa[x, y][0] - pb[x, y][0]) + abs(pa[x, y][1] - pb[x, y][1]) +
               abs(pa[x, y][2] - pb[x, y][2]) for x in range(320) for y in range(176)) \
        / (320 * 176 * 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot-a", help="上镜产物 mp4（取其末帧）")
    ap.add_argument("--shot-b", required=True, help="下镜产物 mp4（取其首帧）")
    ap.add_argument("--ref", help="ref 图（替代 shot-a 时用：二采首帧 vs ref 文件）")
    args = ap.parse_args()
    if not args.shot_a and not args.ref:
        print("[error] 需 --shot-a 或 --ref 之一")
        sys.exit(1)

    tmp_a = "_seam_a.png"
    tmp_b = "_seam_b.png"
    if args.shot_a:
        extract(args.shot_a, tmp_a, tail=True)
    else:
        extract(args.ref, tmp_a)
    extract(args.shot_b, tmp_b)
    d = diff(tmp_a, tmp_b)
    import os
    os.remove(tmp_a)
    os.remove(tmp_b)
    verdict = "接上（同源）" if d < 10 else ("中等（内容近似）" if d < 50 else "断裂")
    print(f"接缝像素差: {d:.1f}/255 → {verdict}")


if __name__ == "__main__":
    main()
