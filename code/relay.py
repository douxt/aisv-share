#!/usr/bin/env python3
"""relay.py — 接力截取统一入口（2026-09-01 配置纪律机制化，开源标准）。

事故史：tail24 截取散落手敲 ffmpeg → 惯例惯性（旧 0.2s 5 帧）vs spec 规定
video_ref_frames（22-24）→ 反复偏离。根治 = 唯一入口 + 参数从 spec 读 +
产物精确校验 + md5 防旧 ref 残留。

用法:
  python diag/relay.py --shot act5_5c2 [--src <mp4>] [--frames N]
  python diag/relay.py --shot act5_5c2 --preview        # 审帧抽帧（spec.preview_times）
  默认 src = H3 output 该 shot 目录最新二采产物（非 draw）
  默认 frames = twopass_spec.json 的 video_ref_frames（24）

产出: shots/shot_battle/{shot}_tail_frame.png + {shot}_tail24.mp4（重编码精确帧数）
      → sync_assets → 帧数 == spec 校验 → md5 打印
"""
import argparse
import json
import os
import subprocess
import sys

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
# SPEC_PATH 来自 common

# Windows GBK 输出崩（2026-08-14 教训）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def spec() -> dict:
    return json.load(open(SPEC_PATH, encoding="utf-8"))


def find_latest_product(shot: str, include_draw: bool = False) -> str | None:
    """H3 output 里该 shot 目录下最新产物（非 sidecar）。
    2026-09-01 修：目录从 spec.env.h3_root 读 + act_* 通配 + include_draw 可选
    （--preview 审帧要 draw 抽卡产物；relay 主模式排除 draw = 只认二采）。"""
    h3_root = H3_ROOT.replace("\\", "/")
    base = os.path.join(h3_root, "ComfyUI", "output", SCENE)
    if not os.path.isdir(base):
        return None
    cands = []
    for act_dir in os.listdir(base):
        p = os.path.join(base, act_dir, shot)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            if not (f.endswith(".mp4") and f.startswith("ref2v")):
                continue
            if not include_draw and "_draw_" in f:
                continue
            fp = os.path.join(p, f)
            cands.append((os.path.getmtime(fp), fp))
    return sorted(cands)[-1][1] if cands else None


def probe_fps(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=r_frame_rate", "-of", "csv", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        num, den = r.stdout.strip().split(",")[-1].split("/")
        return float(num) / float(den)
    except Exception:
        return float(spec().get("relay_fps", 24))


def md5(path: str) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", required=True, help="镜头 key（act5_5c2）")
    ap.add_argument("--src", default="", help="二采产物 mp4（缺省自动找最新）")
    ap.add_argument("--frames", type=int, default=0, help="截取帧数（缺省 = spec.video_ref_frames）")
    ap.add_argument("--preview", action="store_true",
                    help="审帧抽帧模式：按 spec.preview_times 抽 N 帧到 magic/preview/{shot}/")
    ap.add_argument("--only-seeds", default="",
                    help="--preview 过滤：只抽文件名含 run{seed} 的产物（空格分隔；"
                         "2026-09-02 自动管线 — 防旧批 draw 产物污染审帧）")
    args = ap.parse_args()
    only_seeds = [f"run{s}" for s in args.only_seeds.split() if s.strip()]

    s = spec()
    frames = args.frames or int(s["video_ref_frames"])
    tail_sec = float(s["relay_tail_sec"])

    dst = os.path.join(SCENE_DIR, "assets", "shots", "shot_battle")
    os.makedirs(dst, exist_ok=True)
    tail_png = os.path.join(dst, f"{args.shot}_tail_frame.png")
    tail_mp4 = os.path.join(dst, f"{args.shot}_tail24.mp4")

    if args.preview:
        # 2026-09-01 修：遍历 shot 目录全部 draw 产物（审整批 4 发），非单最新
        h3_root = H3_ROOT.replace("\\", "/")
        base = os.path.join(h3_root, "ComfyUI", "output", SCENE)
        srcs = []
        if args.src:
            srcs = [args.src]
        else:
            for act_dir in os.listdir(base):
                p = os.path.join(base, act_dir, args.shot)
                if not os.path.isdir(p):
                    continue
                for f in sorted(os.listdir(p)):
                    if f.endswith(".mp4") and "_draw_" in f:
                        if only_seeds and not any(s in f for s in only_seeds):
                            continue  # 只审本批种子（防旧批污染）
                        srcs.append(os.path.join(p, f))
        if not srcs:
            print(f"[relay] ✗ 源产物未找到: {args.shot}")
            sys.exit(1)
        pdir = os.path.join(SCENE_DIR, "preview", args.shot)
        os.makedirs(pdir, exist_ok=True)
        n = 0
        for src in srcs:
            for t in s["preview_times"]:
                out = os.path.join(pdir, f"{os.path.basename(src)[:-4]}_{t}s.png")
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", src,
                                "-frames:v", "1", out], check=True)
                n += 1
        print(f"[relay] ✓ preview {n} 帧（{len(srcs)} 发 × {len(s['preview_times'])} 时间点）→ {pdir}/")
        return 0

    src = args.src or find_latest_product(args.shot)
    if not src or not os.path.exists(src):
        print(f"[relay] ✗ 源产物未找到: {args.shot}")
        sys.exit(1)
    fps = probe_fps(src)  # 帧率自适应（2026-09-01：不再假设 24fps）
    if fps <= 0:
        print(f"[relay] ✗ 源帧率读取失败: {src}")
        sys.exit(1)

    # 尾帧（relay_tail_sec 单帧）
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", f"-{tail_sec}", "-i", src,
                    "-frames:v", "1", tail_png], check=True)
    # tail24 = 精确 frames 帧（重编码 — -c copy GOP 对齐不可靠，2026-08-31 坑）
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", f"-{frames / fps:.3f}", "-i", src,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-frames:v", str(frames), tail_mp4], check=True)

    # 精确校验帧数 == frames
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames", "-of", "csv", tail_mp4],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    n = int(r.stdout.strip().split(",")[-1])
    if n != frames:
        print(f"[relay] ✗ 帧数 {n} != 规定 {frames} — 重截失败")
        sys.exit(1)

    # 同步到 H3 input（--sync 全量，双份铁律）
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "sync_assets.py"),
                        "--sync"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if r.returncode != 0:
        print(f"[relay] ✗ sync_assets 失败: {r.stdout[-300:]}")
        sys.exit(1)
    print(f"[relay] ✓ {args.shot}: tail_frame + tail24（{n} 帧 = spec {frames}）→ 已同步")
    print(f"[relay]   src: {src}")
    print(f"[relay]   md5: tail_frame={md5(tail_png)} tail24={md5(tail_mp4)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
