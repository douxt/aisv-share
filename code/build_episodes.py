#!/usr/bin/env python3
"""build_episodes.py — 从 docs/*-prompt.md 生成 episodes.json（数据驱动）

单一事实来源 = docs 中的 prompt 文档：
  - YAML frontmatter（shot/act/order/duration/images/audio/deps/...）
  - ```text 块内的六段式 prompt
自动发现全部 *-prompt.md，新增幕只需新建文档，不改本脚本。

漂移检测（2026-08-24，v8 轮播丢失教训）：
  episodes.json = 派生产物，永不手改。重建前逐 shot 对比三种 digest：
  现有 episodes vs 源派生 vs git HEAD。若现有既不是源新版也不是 git 旧版
  （= 被直接手改过，如 update_shot01_v8.py 模式）→ 中止并提示先回写源文档。
  --force 可强制覆盖（自动备份 .bak）。

用法: python build_episodes.py [--force]
"""

import json, os, re, glob, sys, hashlib, shutil, subprocess, argparse

# GBK stdout 重定向崩（2026-08-14 教训 — ⚠️ 等非 GBK 字符在 capture 管道下崩）
# 2026-09-03：被 chain_check/do_prep 子进程 capture 调用必崩 → reconfigure
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
PROJECT = SCENE_DIR
DOCS = os.path.join(SCENE_DIR, "docs")
OUT = os.path.join(SCENE_DIR, "configs", "episodes.json")


def parse_frontmatter(content: str) -> dict:
    """极简 YAML frontmatter 解析（stdlib，支持 str/num/list）。"""
    if not content.startswith("---\n"):
        return {}
    m = re.match(r"^---\n(.*?)\n---", content, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            fm[k] = [x.strip().strip("\"'") for x in inner.split(",")] if inner else []
        elif len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            fm[k] = v[1:-1]
        else:
            try:
                fm[k] = float(v) if "." in v else int(v)
            except ValueError:
                fm[k] = v
    return fm


def extract_prompt(md_path: str) -> str:
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"```text\n(.*?)```", content, re.S)
    if not m:
        raise ValueError(f"{md_path} 中找不到 ```text 块")
    return m.group(1).strip()


def build_shot(fm: dict, prompt: str) -> dict:
    """frontmatter → shot 配置。images[i] → image_i，audio[i] → audio_i。"""
    if "shot" not in fm:
        raise ValueError(f"frontmatter 缺 shot 字段: {fm}")
    cfg = {"prompt": prompt, "duration": float(fm.get("duration", 5))}
    for k in ("aspect_ratio", "act", "order", "deps"):
        if k in fm:
            cfg[k] = fm[k]
    for i, img in enumerate(fm.get("images", [])):
        cfg[f"image_{i}"] = img
    for i, aud in enumerate(fm.get("audio", [])):
        cfg[f"audio_{i}"] = aud
    # 统一输出前缀：magic/{act}/{shot}（2026-08-23 定案：按幕分目录，勿再单独覆盖）
    cfg["filename_prefix"] = f"{SCENE}/{fm.get('act', 'misc')}/{fm['shot']}"
    return cfg


def load_state() -> dict:
    """上次 build 的 shot digest 记录（修复 2026-08-24 假阳性：未 commit 时误判手改）。"""
    p = OUT + ".build_state"
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def git_head_digests() -> dict:
    """git HEAD 的 episodes.json 各 shot digest（state 缺失时的兜底基准）。"""
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:{SCENE}/configs/episodes.json"],
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        if out.returncode != 0:
            return {}
        old = json.loads(out.stdout)
        return {k: hashlib.sha256(v.get("prompt", "").encode("utf-8")).hexdigest()
                for k, v in old.items() if isinstance(v, dict) and v.get("prompt")}
    except Exception:
        return {}


def save_state(shots: dict):
    json.dump({k: hashlib.sha256(v["prompt"].encode("utf-8")).hexdigest()
               for k, v in shots.items()},
              open(OUT + ".build_state", "w", encoding="utf-8"), indent=0)


def detect_drift(shots: dict) -> list[str]:
    """检测 episodes.json 手改漂移（v8 轮播丢失教训 + 2026-08-24 假阳性修复）。

    每 shot 三 digest：现有 episodes(cur) / 源派生(new) / 上次 build(state)。
    漂移判定 = cur 既 != new（源新版）且 cur != state（上次 build 产物）→ 被直接手改过。
    正常重建（只改源文档，无论是否 commit）时 cur == state 或 cur == new → 不拦截。
    """
    if not os.path.exists(OUT):
        return []
    existing = json.load(open(OUT, encoding="utf-8"))
    state = load_state()
    head = git_head_digests() if not state else {}
    drifted = []
    for shot, cfg in shots.items():
        cur = existing.get(shot, {}).get("prompt")
        if cur is None or cur == cfg["prompt"]:
            continue
        cur_d = hashlib.sha256(cur.encode("utf-8")).hexdigest()
        new_d = hashlib.sha256(cfg["prompt"].encode("utf-8")).hexdigest()
        # 合法基准：上次 build 产物（state）或 git HEAD；两者皆无 → 不拦截（迁移期）
        if cur_d == state.get(shot) or cur_d == new_d or cur_d == head.get(shot):
            continue
        if not state and not head:
            continue
        drifted.append(shot)
    return drifted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="检测到手改漂移时强制覆盖（自动备份 .bak）")
    ap.add_argument("--shots", default="",
                    help="子集重建：只重建这些 shot（空格分隔）。其他 shot 的 episodes "
                         "原样保留 = 别镜手改/漂移不被触碰（2026-09-02 P0-2：全量 --force "
                         "曾会静默覆盖未回写源文档的别镜手改）")
    args = ap.parse_args()
    subset = set(args.shots.split()) if args.shots.strip() else None

    shots = {}
    patterns = [os.path.join(DOCS, "*-prompt.md"), os.path.join(DOCS, "prompts", "**", "*.md")]
    md_paths = sorted({p for pat in patterns for p in glob.glob(pat, recursive=True)})
    for md_path in md_paths:
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
        fm = parse_frontmatter(content)
        if not fm:
            raise ValueError(f"{os.path.basename(md_path)} 缺 frontmatter（shot/act/order/...）")
        shot = fm.get("shot", "")
        if subset is not None and shot not in subset:
            continue  # 子集外 md 只读归属，不 build
        prompt = extract_prompt(md_path)
        shots[shot] = build_shot(fm, prompt)
        print(f"  {shot}: act={fm.get('act', '-')} order={fm.get('order', '-')} "
              f"{len(prompt)} chars | images={len(fm.get('images', []))} audio={len(fm.get('audio', []))}")

    # 漂移检测（2026-08-24）：episodes.json 手改防护（子集模式只查子集镜）
    drifted = detect_drift(shots)
    if drifted:
        if not args.force:
            print("⚠️ 漂移检测拦截：episodes.json 存在未回写源文档的手改！", file=sys.stderr)
            print(f"  涉及 shot: {', '.join(drifted)}", file=sys.stderr)
            print("  判定：现有 prompt ≠ 源文档派生 且 ≠ git HEAD = 被直接手改（错误模式）。", file=sys.stderr)
            print("  处置：① 将手改内容写回源文档 docs/*-prompt.md 后重跑 ② 或 --force 强制覆盖（自动备份 .bak）", file=sys.stderr)
            sys.exit(1)
        bak = OUT + ".bak"
        shutil.copy(OUT, bak)
        print(f"⚠️ --force: 备份现有 episodes.json → {os.path.basename(bak)}，继续覆盖")

    # 写入 = merge（子集模式保留子集外 shot 原样；全量模式覆盖全部）
    existing = {}
    if os.path.exists(OUT):
        try:
            existing = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(shots)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=1)
    save_state(shots)
    print(f"Wrote {OUT} ({len(shots)} shots"
          f"{'（子集，全量保留 ' + str(len(existing)) + '）' if subset else ''})")


if __name__ == "__main__":
    main()
