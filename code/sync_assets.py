#!/usr/bin/env python3
"""sync_assets.py — 项目资产 → H3 input 单向同步（2026-08-24，双份一致性机制）

背景：junction 被 H3 拒绝 → 资产 = 真实目录双份副本：
  项目份  <AISV_ROOT>/<SCENE>/assets/
  H3 份   <ComfyUI_H3>/ComfyUI/input/<SCENE>/assets/
替换资产只改一份 = 另一半继续用旧文件（2026-08-24 futa_end_frame 首帧事故）。
本工具 = 标准流程：改项目份 → 跑本工具同步 H3 份。

用法:
  python diag/sync_assets.py --check          # 只输出差异清单，不复制
  python diag/sync_assets.py --sync           # 复制差异文件到 H3 份
  python diag/sync_assets.py --sync --file magic/assets/shots/shot_battle/futa_end_frame.png  # 单文件
"""
import argparse
import hashlib
import os
import shutil
import sys

# Windows GBK 控制台打印 ✓/✗ 崩溃修复（2026-08-27 教训，reconfigure 模板）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
PROJ = os.path.join(SCENE_DIR, "assets")
H3 = os.path.join(H3_ROOT, "ComfyUI", "input", SCENE, "assets")


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def diff_files(files: list[str]) -> list[tuple[str, str, str, str]]:
    """返回 (rel, 状态, 项目md5, H3md5)；状态: OK/DIFF/MISSING_IN_H3。"""
    out = []
    for rel in files:
        pp = os.path.join(PROJ, rel)
        hp = os.path.join(H3, rel)
        if not os.path.exists(hp):
            out.append((rel, "MISSING_IN_H3", "-", "-"))
            continue
        hm = md5(hp)
        if hm != md5(pp):
            out.append((rel, "DIFF", md5(pp), hm))
        else:
            out.append((rel, "OK", md5(pp), hm))
    return out


def main():
    ap = argparse.ArgumentParser(description="项目资产 → H3 input 单向同步")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只对比输出差异清单")
    g.add_argument("--sync", action="store_true", help="复制差异文件到 H3 份")
    ap.add_argument("--file", default=None,
                    help="只处理单个相对路径文件（如 magic/assets/... 相对 assets 的部分）")
    args = ap.parse_args()

    if not os.path.isdir(PROJ):
        print(f"[ERROR] 项目资产目录不存在: {PROJ}")
        raise SystemExit(2)
    if not os.path.isdir(H3):
        print(f"[ERROR] H3 资产目录不存在: {H3}")
        raise SystemExit(2)

    # 收集文件（--file 单文件 或 全量 walk）
    if args.file:
        rel = args.file.replace("\\", "/")
        rel = rel.split(f"{SCENE}/assets/", 1)[-1] if f"{SCENE}/assets/" in rel else rel
        if ".." in rel.split("/"):
            print("[ERROR] --file 不允许包含 ..")
            raise SystemExit(2)
        files = [rel]
    else:
        files = []
        for root, _, fs in os.walk(PROJ):
            for f in fs:
                files.append(os.path.relpath(os.path.join(root, f), PROJ).replace("\\", "/"))

    results = diff_files(files)
    n_bad = sum(1 for _, s, _, _ in results if s != "OK")
    for rel, status, pm, hm in results:
        mark = "✓" if status == "OK" else ("✗" if status == "DIFF" else "?")
        print(f"{mark} [{status:<12}] {rel}  {pm[:8]}{'≠' + hm[:8] if status != 'OK' else ''}")

    if not results:
        print("[ERROR] 未发现任何资产文件 — 检查 PROJ/H3 路径")
        raise SystemExit(2)
    print(f"\n总计 {len(results)} 文件，{n_bad} 个不一致/缺失")

    if args.sync:
        for rel, status, _, _ in results:
            if status == "OK":
                continue
            pp = os.path.join(PROJ, rel)
            hp = os.path.join(H3, rel)
            os.makedirs(os.path.dirname(hp), exist_ok=True)
            shutil.copy2(pp, hp)
            print(f"  → 已同步 {rel}")
        print("同步完成")
    elif n_bad:
        print("\n提示：不一致/缺失文件 — 执行 python diag/sync_assets.py --sync 修复")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
