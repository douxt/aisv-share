#!/usr/bin/env python3
"""sync_code.py — 跟踪 code/ 与上游源仓的漂移（不再自动生成）

code/ 是**fork**：路径已按本包布局改造，后续手工维护。
本工具只做两件事：

  python tools/sync_code.py --check    # 上游文件是否变动（exit 1 = 需要人工合并）
  python tools/sync_code.py --record   # 人工合并后，记录当前上游哈希

上游默认 ../AISV，可用 AISV_SOURCE_ROOT 覆盖。
清单 code/SOURCE.json 只记录相对路径与哈希，不含私有仓名/提交号。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "code"
MANIFEST = DEST / "SOURCE.json"
SRC = Path(os.environ.get("AISV_SOURCE_ROOT") or (ROOT.parent / "AISV"))

MAP = {
    "diag/run_twopass_auto.py": "run_twopass_auto.py",
    "diag/build_twopass.py": "build_twopass.py",
    "diag/relay.py": "relay.py",
    "diag/quality_gate.py": "quality_gate.py",
    "diag/seam_join.py": "seam_join.py",
    "diag/join_clips.py": "join_clips.py",
    "diag/sync_assets.py": "sync_assets.py",
    "diag/chain_check.py": "chain_check.py",
    "diag/audio_check.py": "audio_check.py",
    "diag/upscale_batch.py": "upscale_batch.py",
    "diag/upscale_frames.py": "upscale_frames.py",
    "diag/check_seam.py": "check_seam.py",
    "diag/check_frames.py": "check_frames.py",
    "diag/tail_check.py": "tail_check.py",
    "diag/check_scene_chain.py": "check_scene_chain.py",
    "diag/check_lockframe_desc.py": "check_lockframe_desc.py",
    "magic/scripts/build_episodes.py": "build_episodes.py",
    "diag/twopass_spec.json": "twopass_spec.json",
}


def sha_file(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()[:16]
    except OSError:
        return None


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--record", action="store_true")
    a = ap.parse_args()

    if not SRC.is_dir():
        print(f"源仓不存在: {SRC}（用 AISV_SOURCE_ROOT 指定）")
        return 2

    manifest = load_manifest()
    old = manifest.get("files", {})

    if a.record:
        files = {}
        for src_rel, dest_name in MAP.items():
            h = sha_file(SRC / src_rel)
            if h is None:
                print(f"[WARN] 源缺失，跳过: {src_rel}")
                continue
            files[dest_name] = {"source": src_rel, "sha256": h}
            if not (DEST / dest_name).exists():
                print(f"[WARN] code/ 缺文件: {dest_name}")
        manifest = {
            "synced_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "note": "code/ 是手工维护的 fork；本清单只记录上游快照哈希。",
            "files": files,
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已记录 {len(files)} 个上游文件")
        return 0

    drift = []
    for dest_name, info in old.items():
        cur = sha_file(SRC / info["source"])
        if cur is None:
            drift.append(f"{info['source']}（上游已删除）")
        elif cur != info["sha256"]:
            drift.append(f"{info['source']}（已变动）")
        if not (DEST / dest_name).exists():
            drift.append(f"code/{dest_name}（本地缺失）")
    if drift:
        print("上游漂移，需人工合并后 --record:")
        for d in drift:
            print("  -", d)
        return 1
    print(f"无漂移（{len(old)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
