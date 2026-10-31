#!/usr/bin/env python3
"""check_scene_chain.py — 场景状态继承检查（2026-08-26，L2 机制）

上镜尾帧状态 → 本镜开场状态 逻辑链检查。解决"舞台瞬间变空"类断裂：
只查首帧锁定（描述 vs ref 图）查不出"上镜在场角色去哪了"。

检查项：
  1. scene_inherit 字段必填（frontmatter，L1 设计规范）
  2. 本镜角色登场交代：每角色名出现在 [Shot 1] 描述
  3. 上镜尾帧在场角色 → 本镜（scene_inherit + [Shot 1]）提及去向（保留/退场）
  4. 硬衔接镜：本镜 [Shot 1] 开头必须引用尾帧图（opens on <Picture> / previous shot）

用法：
  python diag/check_scene_chain.py <cur_shot_id> [--prev <shot_id>] [--episodes <path>]
  cur_shot_id 如 act2_2a；--prev 缺省 = episodes.json 按 act/order 前一位
  退出码：0 = pass（含 warn）/ 1 = fail（scene_inherit 缺失）
"""
import argparse
import glob
import json
import os
import re
import sys

# Windows GBK 控制台打印 ✓/✗ 崩溃（2026-08-27 runner preflight 误报修复）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
PROMPTS_DIR = os.path.join(SCENE_DIR, "docs", "prompts")
EPISODES_DEFAULT = os.path.join(SCENE_DIR, "configs", "episodes.json")
CHARACTERS_DIR = os.path.join(SCENE_DIR, "assets", "characters")

# 出场动作词（本镜新角色必须带登场交代）
INTRO_VERBS = [
    "leaps", "leaps out", "emerges", "enters", "dashes", "dashes in", "appears",
    "lands", "arrives", "steps out", "steps onto", "walks in", "rushes in",
    "drops in", "drops onto", "vaults", "jumps", "slips in", "moves in",
]
# 退场/位移交代词（上镜角色去向；scene_inherit 可中文，两种都查）
EXIT_PATTERNS = [
    r"back[s]? away", r"withdraw", r"retreat", r"forced back", r"pushed back",
    r"staggers", r"leaves the stage", r"out of frame", r"out of view",
    r"exit[s]?", r"falls back", r"stumbles back", r"drops back", r"retreating",
    r"shield[s]?.*back", r"back[s]?.*stage wing", r"disappear", r"vanishes",
    r"退场", r"退出画", r"出画", r"撑盾后退", r"退下台",
]
# 状态延续词（硬衔接镜开场必须引用上镜尾帧）
HARD_LINK_PATTERNS = [
    r"opens on", r"first frame", r"frozen", r"previous shot", r"tail frame",
    r"<Picture \d+>", r"the light burst", r"the burst", r"thaws", r"embers",
    r"fading", r"dimming", r"still hanging", r"left over", r"remnants", r"lingering",
]

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S | re.M)
SHOT1_RE = re.compile(r"\[Shot 1\](.{0,1200})", re.S)


def find_md(shot_id: str) -> str | None:
    """扫描 prompts/**/*.md，frontmatter shot: <id> 匹配 → 路径。"""
    for p in glob.glob(os.path.join(PROMPTS_DIR, "**", "*.md"), recursive=True):
        m = FRONTMATTER_RE.match(open(p, encoding="utf-8").read())
        if m and re.search(rf"^shot:\s*{re.escape(shot_id)}\s*$", m.group(1), re.M):
            return p
    return None


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^(\w+):\s*(.*)$", line.strip())
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def role_from_path(p: str, known_roles: set) -> str | None:
    """magic/assets/characters/<role>/... 或 voices/tts_<role>.mp3 → role。"""
    p = p.replace("\\", "/")
    if "/characters/" in p:
        mm = re.search(r"/characters/([^/]+)/", p)
        if mm and mm.group(1) in known_roles:
            return mm.group(1)
    if "/voices/" in p:
        base = os.path.splitext(os.path.basename(p))[0]
        for prefix in ("tts_", "voice_"):
            if base.startswith(prefix) and base[len(prefix):] in known_roles:
                return base[len(prefix):]
        # futa_voice_ref.mp3 → futa
        mm = re.match(r"^([a-z_]+?)(?:_voice.*)?$", base)
        if mm and mm.group(1) in known_roles:
            return mm.group(1)
    return None


def shot_roles(ep: dict, known_roles: set) -> set:
    roles = set()
    for k, v in ep.items():
        if k.startswith("image_") or k.startswith("audio_"):
            r = role_from_path(v, known_roles)
            if r:
                roles.add(r)
    return roles


def detailed_desc(prompt: str) -> str:
    """detailed_description 段（到 overall_soundscape 为止）。"""
    if "detailed_description:" not in prompt:
        return prompt
    dd = prompt.split("detailed_description:", 1)[1]
    dd = dd.split("overall_soundscape:")[0]
    return dd


def subject_map(prompt: str, known_roles: set) -> dict:
    """subject_definitions 段 → {Subject N: role}。描述里用 <Subject N> 标签，正文无角色名。"""
    sd = prompt.split("subject_definitions:", 1)[1].split("summary:", 1)[0]
    out = {}
    for m in re.finditer(r"<Subject (\d+)> is ([a-zA-Z ]+?),", sd):
        name = m.group(2).strip().split()[0].lower()
        if name in known_roles:
            out[int(m.group(1))] = name
    return out


def tail_subjects(dd: str) -> set:
    """detailed_description 最后 [Shot N] 段里出现的 <Subject N>。"""
    parts = re.findall(r"\[Shot \d+\].{0,2000}", dd, re.S)
    seg = parts[-1] if parts else dd[:1500]
    return {int(n) for n in re.findall(r"<Subject (\d+)>", seg)}


def shot1_opening(prompt: str) -> str:
    """detailed_description 内第一个 [Shot 1] 段开头。"""
    dd = detailed_desc(prompt)
    m = SHOT1_RE.search(dd)
    return m.group(1) if m else dd[:1200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cur_shot")
    ap.add_argument("--prev", default=None)
    ap.add_argument("--episodes", default=EPISODES_DEFAULT)
    args = ap.parse_args()

    try:
        src = json.load(open(args.episodes, encoding="utf-8"))
    except Exception as e:
        print(f"FAIL: 无法读 episodes.json: {e}")
        return 1
    shots = src.get("shots", src)

    if args.cur_shot not in shots:
        print(f"FAIL: episodes.json 无场景 {args.cur_shot}")
        return 1

    known_roles = {d for d in os.listdir(CHARACTERS_DIR) if os.path.isdir(os.path.join(CHARACTERS_DIR, d))}

    cur_ep = shots[args.cur_shot]
    cur_md = find_md(args.cur_shot)
    fm = parse_frontmatter(open(cur_md, encoding="utf-8").read()) if cur_md else {}

    fails, warns = [], []

    # 1. scene_inherit 必填（L1 规范）
    scene_inherit = fm.get("scene_inherit", "")
    if not scene_inherit:
        fails.append("frontmatter 缺 scene_inherit（上镜尾帧状态 → 本镜开场状态 → 变化路径）— 设计 GATE 必填")
    else:
        print(f"  ✓ scene_inherit: {scene_inherit[:80]}...")

    # 2. 本镜角色登场交代（Subject 标签映射，描述用 <Subject N>）
    cur_roles = shot_roles(cur_ep, known_roles)
    cur_map = subject_map(cur_ep["prompt"], known_roles)
    opening = shot1_opening(cur_ep["prompt"])
    named = {n for n, r in cur_map.items() if r in cur_roles}
    for n in sorted(named):
        if f"<Subject {n}>" not in opening:
            warns.append(f"角色 '{cur_map[n]}'(<Subject {n}>) 登场未在本镜 [Shot 1] 描述交代")
        elif not any(v in opening.lower() for v in INTRO_VERBS):
            # 无登场动作词 = 可能直接"站在那"（硬衔接保留场景可接受，轻提）
            warns.append(f"角色 '{cur_map[n]}' 在 [Shot 1] 有引用但无登场动作词（leaps/enters 等）— 硬衔接保留可忽略")
    for r in sorted(cur_roles - set(cur_map.values())):
        warns.append(f"角色 '{r}' 无 <Subject N> 映射（subject_definitions 未定义或名字不匹配）")

    # 3. 上镜尾帧角色去向
    prev_id = args.prev
    if not prev_id:
        # act/order 推导前一位
        act, order = cur_ep["act"], int(cur_ep["order"])
        candidates = [sid for sid, ep in shots.items()
                      if ep.get("act") == act and int(ep.get("order", 99)) == order - 1]
        if len(candidates) == 1:
            prev_id = candidates[0]
    if prev_id and prev_id in shots:
        prev_ep = shots[prev_id]
        prev_map = subject_map(prev_ep["prompt"], known_roles)
        tail_ns = tail_subjects(detailed_desc(prev_ep["prompt"]))
        tail_roles = {prev_map[n] for n in tail_ns if n in prev_map}
        if tail_roles:
            # cur 侧角色以 <Subject N> 标签出现（正文无角色名）；scene_inherit 用角色名
            cur_by_role = {r: f"<Subject {n}>" for n, r in cur_map.items()}
            cur_text = scene_inherit.lower() + " " + opening.lower()
            for r in sorted(tail_roles):
                tag = cur_by_role.get(r, "")
                mentioned = tag.lower() in opening.lower() if tag else r in cur_text
                if not mentioned:
                    warns.append(
                        f"上镜({prev_id})尾帧在场角色 '{r}' 未在本镜开场/场景继承交代去向 "
                        f"（保留？退场？凭空消失=断裂）")
            # 上镜尾帧角色在本镜不在场 → scene_inherit 必须有退场交代词
            gone = tail_roles - cur_roles
            if gone and scene_inherit and not any(re.search(pat, scene_inherit.lower()) for pat in EXIT_PATTERNS):
                warns.append(f"上镜尾帧角色 {'/'.join(sorted(gone))} 本镜不在场 — scene_inherit 未写退场方式（exit/forced back/out of frame 等）")
        print(f"  ✓ 上镜 {prev_id} 尾帧角色: {sorted(tail_roles) if tail_roles else '无角色提及'}")
    elif prev_id:
        warns.append(f"上镜 {prev_id} 不在 episodes.json，跳过去向检查")

    # 4. 硬衔接镜：开场引用尾帧
    is_hard = any("/shots/" in v for k, v in cur_ep.items() if k.startswith("image_"))
    if is_hard:
        if not any(re.search(p, opening.lower()) for p in HARD_LINK_PATTERNS):
            fails.append("硬衔接镜（images 含尾帧图）— [Shot 1] 开头未引用尾帧（opens on <Picture N> / previous shot / the frozen frame 等）")
        print("  ✓ 硬衔接：开场引用尾帧 ✓" if any(re.search(p, opening.lower()) for p in HARD_LINK_PATTERNS) else "  ✗ 硬衔接开场未引用尾帧")

    for w in warns:
        print(f"  ⚠ {w}")
    for f in fails:
        print(f"  ✗ {f}")
    print(f"== {args.cur_shot}: {'FAIL' if fails else 'PASS'}{f' + {len(warns)} warn' if warns else ''} ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
