#!/usr/bin/env python3
"""audio_check.py — 抽卡音频质量检测（2026-08-28 4b 破音教训固化）

每发检测：峰值削波（clip 计数）、整体 RMS、台词窗 RMS。
判定：peak > 0dB 或 clip count > 0 = CLIP（破音）；台词窗 RMS < -30dB = 台词弱。

用法: python diag/audio_check.py <mp4...> [--window S:E]
"""
import argparse
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def audio_stats(f: str) -> dict:
    out = subprocess.run(["ffmpeg", "-i", f, "-map", "0:a",
                          "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    peak = re.findall(r"Peak level dB: ([\-0-9.]+)", out)
    rms = re.findall(r"RMS level dB: ([\-0-9.]+)", out)
    # 真削波判定：峰值超 0dB（H3 生成偶发破音实证 = +0.06dB）
    clipped = any(float(p) > 0.0 for p in peak)
    return {"peak": float(peak[0]) if peak else None,
            "rms": float(rms[0]) if rms else None,
            "clipped": clipped}


def window_rms(f: str, start: float, dur: float) -> float | None:
    out = subprocess.run(["ffmpeg", "-i", f, "-map", "0:a", "-ss", str(start),
                          "-t", str(dur), "-af", "astats=metadata=1:reset=0",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.findall(r"RMS level dB: ([\-0-9.]+)", out)
    return float(m[0]) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--window", default="4.5:6.5", help="台词窗 S:E（默认 4.5:6.5）")
    args = ap.parse_args()
    ws, we = [float(x) for x in args.window.split(":")]
    print(f"{'file':<45} {'peak':>7} {'RMS':>7} {'台词窗RMS':>9}  判定")
    print("-" * 90)
    for f in args.files:
        s = audio_stats(f)
        wr = window_rms(f, ws, we)
        name = f.split("/")[-1]
        verdict = "无声/探测失败" if s["peak"] is None else (
            "CLIP破音" if s["clipped"] else ("台词弱" if wr is not None and wr < -30 else "OK"))
        peak_s = f"{s['peak']:>+7.2f}" if s["peak"] is not None else f"{'n/a':>7}"
        rms_s = f"{s['rms']:>+7.1f}" if s["rms"] is not None else f"{'n/a':>7}"
        wr_s = f"{wr:>+9.1f}" if wr is not None else f"{'n/a':>9}"
        print(f"{name:<45} {peak_s} {rms_s} {wr_s}  {verdict}")


if __name__ == "__main__":
    main()
