#!/usr/bin/env python3
"""tail_check.py — 二采产物末帧花屏检测（2026-08-25 根因诊断工具）

量化判定：末帧 lap 低 = 花屏；记录花屏起始帧位（相对总帧数）。

用法:
  python diag/tail_check.py <mp4> [<mp4> ...]
输出:
  帧数/时长/lap 剖面（首/中/末）+ 末 12 帧逐帧 lap + 花屏判定 + 花屏起始帧位

判定标准（2026-08-25 定）:
  正常 = 末帧 lap >= 50；花屏 = 末帧 lap < 10 且 突变 >= 10 倍（防误判）
"""
import sys
import cv2


def analyze(f: str) -> dict:
    cap = cv2.VideoCapture(f)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    def lap_at(pos: int) -> float:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        fr = cap.read()[1]
        if fr is None:
            return -1.0
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        return cv2.Laplacian(gray, cv2.CV_64F).var()

    if n <= 0 or fps <= 0:
        print(f"{f}\n  frames={n} fps={fps} — 解码失败，判定: ERROR")
        return {"file": f, "frames": n, "last_lap": -1, "mid_lap": -1,
                "flaky_start": 0, "verdict": "ERROR"}
    mid = max(n // 2, 0)
    first, midv, last = lap_at(10), lap_at(mid), lap_at(n - 3)
    tail = [lap_at(i) for i in range(max(0, n - 12), n)]

    # 花屏起始帧位：从末往前找第一个正常帧（>= 30）→ 其后为花屏区
    start = None
    for i in range(n - 1, -1, -1):
        if lap_at(i) >= 30:
            start = i + 1
            break
    if start is None:
        start = 0

    mutated = last < 10 and mid > 0 and last / max(mid, 1e-6) < 0.1
    verdict = "FLAKY" if (last < 10 and mutated) else "OK"

    print(f"{f}")
    print(f"  frames={n} dur={n/fps:.1f}s fps={fps:.1f}")
    print(f"  lap: 首={first:.0f} 中={midv:.0f} 末={last:.0f}")
    print(f"  末12帧: " + " ".join(f"{v:.0f}" for v in tail))
    print(f"  花屏起始帧位: {start}/{n} ({start/n*100:.0f}%)  判定: {verdict}")
    return {"file": f, "frames": n, "last_lap": last, "mid_lap": midv,
            "flaky_start": start, "verdict": verdict}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    rc = 0
    for f in sys.argv[1:]:
        if analyze(f).get("verdict") != "OK":
            rc = 1
    sys.exit(rc)
