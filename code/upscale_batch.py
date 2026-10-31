#!/usr/bin/env python3
"""upscale_batch.py — 全片批超分统一入口（2026-09-02 配置纪律机制化）。

流程（Real-CUGAN 2x 最佳实践，session-2026-08-30 §2）：
  逐镜：upscale_frames（分帧 fp16 batch=spec）→ 2x = 2560x1408
         → 降采样 spec.upscale_target（1920x1080）
  → join_clips 重拼（累积 offset 公式固化）

注意：GPU 排他（H3 空闲才跑）；逐镜处理磁盘可控（单镜帧 ~500MB 临时）。

用法:
  python diag/upscale_batch.py --out magic/output/全片_1080p.mp4
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="输出 1080p mp4")
    ap.add_argument("--clips", nargs="*", default=[],
                    help="源片段列表（缺省 = 自动扫 25 镜序列）")
    ap.add_argument("--no-join", action="store_true", help="只超分不重拼（调试）")
    args = ap.parse_args()

    s = spec()
    h3 = H3_ROOT.replace("\\", "/")
    O = os.path.join(h3, "ComfyUI", "output", SCENE)
    import join_clips
    clips = args.clips or [os.path.join(O, rel) for rel in join_clips.FULL_FILM]
    target = s["upscale_target"].lower()  # 1920x1080
    tw, th = target.split("x")
    tmp_dir = os.path.join(SCENE_DIR, "output", "_up2x_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    up_clips = []
    up_frames = os.path.join(SCRIPT_DIR, "upscale_frames.py")
    for i, c in enumerate(clips):
        if not os.path.exists(c):
            print(f"[upscale_batch] ✗ 缺失: {c}")
            sys.exit(1)
        up = os.path.join(tmp_dir, f"up_{i:02d}.mp4")
        down = os.path.join(tmp_dir, f"down_{i:02d}.mp4")
        r = subprocess.run([sys.executable, up_frames, "--input", c, "--out", up,
                            "--batch", str(s.get("upscale_batch", 2))],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800)
        if r.returncode != 0:
            print(f"[upscale_batch] ✗ 超分失败 {os.path.basename(c)}: {r.stdout[-300:]}")
            sys.exit(1)
        # 降采样到 target
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", up,
                            "-vf", f"scale={tw}:{th}", "-c:v", "libx264", "-crf", "18",
                            "-c:a", "aac", "-b:a", "192k", down],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        if r.returncode != 0:
            print(f"[upscale_batch] ✗ 降采样失败 {os.path.basename(c)}: {r.stderr[-300:]}")
            sys.exit(1)
        os.remove(up)
        up_clips.append(down)
        print(f"[upscale_batch] ✓ {i+1}/{len(clips)} {os.path.basename(c)}")
    if args.no_join:
        print(f"[upscale_batch] 完成（不拼接）：{len(up_clips)} 镜在 {tmp_dir}")
        return 0
    if len(up_clips) == 1:
        # 单输入（已拼好的合并片）→ 直接改名输出，不走 join（join ≥2 段，
        # 2026-09-02 自动管线 pilot 超分单文件踩坑）
        import shutil
        shutil.move(up_clips[0], args.out)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"[upscale_batch] ✓ {args.out}（单输入直出）")
        return 0
    join_clips.join(up_clips, args.out, float(s["seam_xfade_sec"]))
    print(f"[upscale_batch] ✓ {args.out}")
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
