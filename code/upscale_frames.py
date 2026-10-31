"""upscale_frames.py — 逐帧 Real-CUGAN 2x 超分（内存安全版，2026-08-31）
OOM 教训：VHS 整段超分 30s = 124GB CPU 分配失败（虚拟内存拖垮系统）。
本脚本：ffmpeg 拆帧 → 分批 16 帧 GPU 超分 → 重拼 mp4（音频透传）。

用法: python upscale_frames.py --input a.mp4 --out b.mp4 [--batch 16]
"""
import argparse, subprocess, os, shutil, tempfile
import numpy as np
import torch
from spandrel import ModelLoader

from common import UPSCALE_MODEL as MODEL

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=0)  # 2026-09-02 修：OOM 事故修复落进默认值（fp16 单帧 ~7GB → 2 帧 ~14GB/16GB 安全；16 = 112GB OOM 复发）
    args = ap.parse_args()
    if args.batch <= 0:
        import json as _json
        from common import SPEC_PATH as _sp
        args.batch = int(_json.load(open(_sp, encoding="utf-8")).get("upscale_batch", 2))

    tmp = tempfile.mkdtemp(prefix="up2x_")
    frames_dir = os.path.join(tmp, "f")
    os.makedirs(frames_dir)

    # 1. 拆帧（含音频提取）
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.input,
                    f"{frames_dir}/f%06d.png"], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.input,
                    "-vn", "-c:a", "pcm_s16le", os.path.join(tmp, "audio.wav")], check=True)
    names = sorted(os.listdir(frames_dir))
    print(f"{len(names)} frames")

    # 2. 加载模型（fp16 省显存：Real-CUGAN 单帧 1280x704 峰值 ~14GB fp32 → fp16 ~7GB）
    model = ModelLoader().load_from_file(MODEL).eval()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(dev)
    if dev == "cuda":
        model = model.half()
    from PIL import Image

    # 3. 分批超分（--batch 默认 2：fp16 单帧 ~7GB 显存 → 2 帧 ~14GB/16GB 卡安全；
    #    empty_cache 按批清（每帧清 = 巨大开销，实测 ~2x 慢）
    out_dir = os.path.join(tmp, "o")
    os.makedirs(out_dir)
    torch.backends.cudnn.benchmark = True
    for i in range(0, len(names), args.batch):
        batch = names[i:i + args.batch]
        arrs = []
        for n in batch:
            a = np.array(Image.open(os.path.join(frames_dir, n)).convert("RGB"))
            arrs.append(torch.from_numpy(a).permute(2, 0, 1).float().div(255.))
        x = torch.stack(arrs).to(dev)
        if dev == "cuda":
            x = x.half()
        with torch.no_grad():
            y = model(x).clamp(0, 1).float().cpu()
        for n, f in zip(batch, y):
            im = (f.permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
            Image.fromarray(im).save(os.path.join(out_dir, n))
        if dev == "cuda":
            torch.cuda.empty_cache()
        if i % 120 == 0:
            print(f"{i}/{len(names)}")
    print("upscaled")

    # 4. 重拼（fps 从源取）
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                            args.input], capture_output=True, text=True)
    fps = probe.stdout.strip().split("/")
    fps_v = round(int(fps[0]) / int(fps[1]), 3)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps_v),
                    "-i", f"{out_dir}/f%06d.png", "-i", os.path.join(tmp, "audio.wav"),
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", args.out], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("done:", args.out)

if __name__ == "__main__":
    main()
