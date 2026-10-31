#!/usr/bin/env python3
"""chain_check.py — 源↔模板↔产物链一致性门禁（L1-L5 实施，2026-09-03）

事故背景：2b 夜跑 = md prompt 改而未 rebuild → 模板 stale 静默续用（12 卡废）。
本模块把「自觉 rebuild」变成代码内必经点：使用前链检查 + 自动同步（L1）+
手改冲突 fail-closed（L2）+ 产物指纹（L3）。

依赖纪律（hetero 评审 high#1）：**零项目内 import** — 不 import rt/auto_pipeline
（rt 将 import 本模块 → 反向循环禁止）。模板路径/构建器路径全部下沉为参数。
唯一例外 = 复用 magic/scripts/build_episodes.py 的 parse_frontmatter/build_shot
（生成器/检查器共享同一解析逻辑 — CodeWhale facts-lib 模式，禁止双实现）。

链事实：md(源) --build_episodes--> episodes.json --build_twopass--> 模板(embed)。
同步态 ⟺ md text 块 == episodes prompt == 模板 embed（均逐字复制）。
frontmatter 字段（images/video_ref/duration/...）走 episodes cfg 同步比对。

边界（诚实声明）：frontmatter 仅改 + 直接手敲 rt 裸跑 = fm 比对仍抓（见
fm_drift）；唯一漏网 = md 未动但 episodes/模板被外力改回旧值 — 由 git 兜底。

用法:
  python diag/chain_check.py check --shot act2_2a \
      --md magic/docs/prompts/act2/2a.md \
      --episodes magic/configs/episodes.json \
      --template diag/api_workflow_twopass_legacy_act2_2a_fl2v11.json [...]
  python diag/chain_check.py reconcile <同 check 参数> [--python <exe>] [--dry]
  python diag/chain_check.py backfill --dir <H3 output 场景目录>
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- 构建期字段（与 md_digest_of 一致 — 改这里必须同步两处注释） ----
BUILD_FM_FIELDS = ("shot", "act", "order", "duration", "aspect_ratio",
                   "images", "audio", "video_ref", "deps")
# 模板 embed 判据（preflight 旧 STALE 块同款 — 合一双判据：中英两代文本都含）
EMBED_MARKERS = ("subject_definitions", "感谢祭")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def read_text(path: str) -> str:
    return open(path, encoding="utf-8").read()


# ---------------- digest 层（auto_pipeline 同名函数迁入 + 路径参数化） ----------------

def md_digest_of(md_rel: str) -> str:
    """build 相关内容 digest = frontmatter build 字段 + ```text prompt 块。
    修改记录（## 修改记录 节）不算 build 输入 — 改了不触发 rebuild（2026-09-02 精确化）。
    用途：auto_pipeline st.md_digest 变更检测（含 fm — fm 只改也触发）。"""
    txt = read_text(os.path.join(AISV_ROOT, md_rel))
    keep = []
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if m:
        fm_lines = []
        for line in m.group(1).splitlines():
            k = line.split(":", 1)[0].strip()
            if k in BUILD_FM_FIELDS:
                fm_lines.append(line)
        keep.append("\n".join(fm_lines))
    tb = re.search(r"```text\n(.*?)```", txt, re.S)
    if tb:
        keep.append(tb.group(1))
    return sha("\n".join(keep))


def md_prompt_text(md_rel: str) -> str:
    """```text 块内容（复用 build_episodes.extract_prompt — 含 .strip()；双实现禁止）"""
    return _be_module().extract_prompt(os.path.join(AISV_ROOT, md_rel))


def episodes_digest(episodes_path: str, shot: str) -> str | None:
    """episodes.json shots[shot].prompt digest（无此镜 = None）。"""
    try:
        src = json.load(open(os.path.join(AISV_ROOT, episodes_path), encoding="utf-8"))
        shots = src.get("shots", src)
        p = shots.get(shot, {}).get("prompt")
        return sha(p) if p else None
    except Exception:
        return None


def template_embed_digest(template_path: str) -> str | None:
    """模板内嵌 prompt digest — 判据统一：扫全 string 节点含 EMBED_MARKERS 之一。
    （preflight 旧 STALE 块判据迁移；多命中取首个，另注警告 — 正常链每模板只嵌一份）"""
    if not os.path.exists(template_path):
        return None
    wf = json.load(open(template_path, encoding="utf-8"))
    embeds = []
    for nid, n in wf.items():
        for k, v in n.get("inputs", {}).items():
            if isinstance(v, str) and any(m in v for m in EMBED_MARKERS):
                embeds.append(v)
    if not embeds:
        return None
    return sha(embeds[0])


def _scan_mds(roots=None) -> list[str]:
    """全部源 md（含 frontmatter shot）项目相对路径，canonical 优先排序。
    canonical = 位于 …/prompts/… 子目录（生产目录 — magic/docs/pilot-prompt.md 类
    档案文件同 shot 时排在后面；2026-09-03 事故：pilot 旧文曾抢先被 resolve_md
    命中 → rt.run_one auto-reconcile 差点拿旧文重建）。"""
    roots = roots or [f"{SCENE}/docs"]
    out = []
    for root in roots:
        base = os.path.join(AISV_ROOT, root)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    if parse_fm(p).get("shot"):
                        out.append(os.path.relpath(p, AISV_ROOT).replace("\\", "/"))
                except Exception:
                    continue
    return sorted(out, key=lambda p: (0 if "/prompts/" in p else 1, p))


def resolve_md(shot: str, roots=None) -> str | None:
    """shot key → 源 md（canonical prompts/ 优先）。返回项目相对路径。"""
    for md_rel in _scan_mds(roots):
        if parse_fm(os.path.join(AISV_ROOT, md_rel)).get("shot") == shot:
            return md_rel
    return None


def all_mds(roots=None) -> list[str]:
    return _scan_mds(roots)


def chain_variants(spec_path: str | None = None) -> list[str]:
    """本镜（链型 legacy）模板变体名 = spec label 派生（唯一实现 — auto_pipeline
    tp_variant/draw_variant 同源；chain_check 不 import rt → 公式集中于此）。"""
    sp = json.load(open(spec_path or SPEC_PATH,
                        encoding="utf-8"))
    lab = sp.get("label", "")
    return [f"ref2v_{lab}", f"ref2v_draw_{lab}"]


def shot_templates(shot: str, spec_path: str | None = None) -> list[str]:
    """镜的**实际会用**模板（每 variant 至多一个：legacy_ 优先 — build_twopass c5a7fd8
    起只写 legacy_ 前缀；无前缀旧文件 = 残留不再生成，不计入链（rt.template_path 同语义
    下沉 — 2026-09-03 双实现消除）。全部缺失 → 返回 legacy 候选路径（报"未 build"）。"""
    found = []
    for stem in chain_variants(spec_path):
        cands = [
            os.path.join(SCRIPT_DIR, f"api_workflow_twopass_legacy_{shot}_{stem}.json"),
            os.path.join(SCRIPT_DIR, f"api_workflow_twopass_legacy_{stem}.json"),
            os.path.join(SCRIPT_DIR, f"api_workflow_twopass_{shot}_{stem}.json"),
            os.path.join(SCRIPT_DIR, f"api_workflow_twopass_{stem}.json"),
        ]
        found.append(next((c for c in cands if os.path.exists(c)), cands[0]))
    return found


# ---------------- fm 层（共享 build_episodes 解析 — 不双实现） ----------------

def _be_module():
    """build_episodes.py 以模块载入（stdlib only → 安全单向依赖）。"""
    be_dir = SCRIPT_DIR
    if be_dir not in sys.path:
        sys.path.insert(0, be_dir)
    import build_episodes  # noqa: PLC0415 — 延迟导入避免循环（build_episodes 不 import 本模块）
    return build_episodes


def parse_fm(md_path: str) -> dict:
    return _be_module().parse_frontmatter(read_text(md_path))


def fm_drift(episodes_path: str, shot: str, md_rel: str) -> list[str]:
    """md frontmatter 派生字段 vs episodes.json cfg 逐键比对（含缺失/残留双向）。
    复用 build_episodes.build_shot = 生成器与检查器同一份逻辑。
    prompt 本身不在此比（digest 层覆盖）。返回漂移描述列表（空 = 同步）。"""
    md_path = os.path.join(AISV_ROOT, md_rel)
    be = _be_module()
    fm = be.parse_frontmatter(read_text(md_path))
    if not fm:
        return ["md 无 frontmatter"]
    fresh = be.build_shot(fm, md_prompt_text(md_rel))
    try:
        src = json.load(open(os.path.join(AISV_ROOT, episodes_path), encoding="utf-8"))
        shots = src.get("shots", src)
        stored = shots.get(shot)
    except Exception:
        return ["episodes.json 读失败"]
    if stored is None:
        return ["episodes 无此镜"]
    drift = []
    for k, v in fresh.items():
        if k in ("prompt", "filename_prefix"):
            continue  # prompt = digest 层；filename_prefix 纯派生
        if stored.get(k) != v:
            drift.append(f"{k}: episodes {stored.get(k)!r} != md 派生 {v!r}")
    stale = [k for k in stored if (k.startswith(("image_", "audio_")) or
                                   k in ("prompt", "duration", "filename_prefix")) is False
             and k not in fresh]
    for k in stale:
        drift.append(f"stale key {k}: episodes 仍有（md 已删）")
    return drift


# ---------------- 链状态 + L1 自动同步 + L2 fail-closed ----------------

def chain_state(shot: str, md_rel: str, episodes_path: str,
                templates: list[str]) -> dict:
    """三层纯读快照。ok = prompt 脊柱三层等 + fm 无漂移 + 每模板 embed 存在。
    templates = 该镜全部变体模板路径（tp + draw）。"""
    md_text_d = sha(md_prompt_text(md_rel))
    ep_d = episodes_digest(episodes_path, shot)
    layers = {"md_prompt": md_text_d, "episodes": ep_d}
    for t in templates:
        layers[f"tmpl:{os.path.basename(t)}"] = template_embed_digest(t)
    broken = []
    if ep_d is None:
        broken.append("episodes 无此镜 prompt")
    elif ep_d != md_text_d:
        broken.append("episodes prompt != md text 块（源改未 build）")
    for t in templates:
        td = layers[f"tmpl:{os.path.basename(t)}"]
        if td is None:
            broken.append(f"{os.path.basename(t)} 无 embed（未 build 或模板异常）")
        elif ep_d is not None and td != ep_d:
            broken.append(f"{os.path.basename(t)} embed != episodes（手改/stale）")
    fm_bad = fm_drift(episodes_path, shot, md_rel)
    ok = not broken and not fm_bad
    return {
        "shot": shot,
        "md": os.path.basename(md_rel),
        "layers": layers,
        "fm_drift": fm_bad,
        "broken": broken,
        "ok": ok,
        "fix_cmd": "" if ok else (
            "python diag/chain_check.py reconcile --shot %s --md %s "
            "--episodes %s --template %s" % (
                shot, md_rel, episodes_path, " ".join("--template " + t for t in templates))
            if "手改" not in " ".join(broken) + " ".join(fm_bad)
            else "模板/ episodes 疑似手改 — git diff 核实后 checkout 或回写源文档再 reconcile"),
    }


def auto_reconcile(shot: str, md_rel: str, episodes_path: str,
                   templates: list[str], *, python_exe: str | None = None,
                   dry: bool = False, force: bool = False, log=print) -> dict:
    """L1 核心：使用点自动同步。L2：手改冲突不覆盖 fail-closed。

    流程（评审修正版）：
      1. 同步 → ok 早退。
      2. 模板 embed != episodes 现值（手改迹象）→ **不覆盖** → False（L2）。
         例外：模板缺文件 = 未 build → 走重建。
      3. md 动了（episodes != md）→ build_episodes 子集；漂移 exit1 → 子集 --force
         （.bak 备份，只碰本镜 — P0-2）。
      4. build_twopass --shot [--video-ref md fm]。
      5. 重验全层 → 仍断 = False。
    返回 {ok, reason, actions[]}。
    """
    acts: list[str] = []
    py = python_exe or sys.executable
    st = chain_state(shot, md_rel, episodes_path, templates)
    if st["ok"]:
        return {"ok": True, "reason": "chain in sync", "actions": acts, "state": st}

    ep_d = st["layers"]["episodes"]
    # L2：模板存在但 embed != episodes 现值 → 模板出带（手改/历史残留）→ 不覆盖
    # （人工核实后 --force 放行 — 逃生门语义，agent 纪律文档化）
    if not force:
        for t in templates:
            if os.path.exists(t):
                td = template_embed_digest(t)
                if ep_d is not None and td != ep_d:
                    return {"ok": False,
                            "reason": f"L2 fail-closed: {os.path.basename(t)} embed 与 "
                                      f"episodes 不一致（疑手改/旧代残留）— 不自动覆盖。"
                                      f"git diff 模板核实；确认后 --force",
                            "actions": acts, "state": st}

    # L1：源动了 → 自动 rebuild（build_episodes 子集 → build_twopass [--video-ref]）
    be = os.path.join(SCRIPT_DIR, "build_episodes.py")
    bt = os.path.join(SCRIPT_DIR, "build_twopass.py")
    fm = parse_fm(os.path.join(AISV_ROOT, md_rel)) if not dry else {}
    vr_arg = ["--video-ref", str(fm["video_ref"])] if fm.get("video_ref") else []
    steps = [(be, ["--shots", shot]), (bt, ["--shot", shot, *vr_arg])]
    if dry:
        for _b, extra in steps:
            acts.append(f"[dry] {py} {_b} {' '.join(extra)}")
    else:
        for base, extra in steps:
            r = subprocess.run([py, base, *extra], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            if r.returncode != 0:
                # build_episodes 漂移拦截 → 子集 --force（备份 .bak）
                if base == be:
                    log(f"[chain_check] build_episodes 漂移 → 子集 --force: "
                        f"{(r.stderr or r.stdout)[-200:]}")
                    r = subprocess.run([py, base, "--shots", shot, "--force"],
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=600)
                if r.returncode != 0:
                    return {"ok": False,
                            "reason": f"{os.path.basename(base)} 失败 exit {r.returncode}: "
                                      f"{(r.stderr or r.stdout)[-300:]}",
                            "actions": acts, "state": st}
            acts.append(f"{py} {os.path.relpath(base, AISV_ROOT)} "
                        f"{' '.join(extra)}".replace("\\", "/"))

    st2 = chain_state(shot, md_rel, episodes_path, templates)
    if not st2["ok"]:
        return {"ok": False,
                "reason": f"rebuild 后仍断链: {st2['broken']} {st2['fm_drift']}",
                "actions": acts, "state": st2}
    return {"ok": True, "reason": "auto-reconcile: rebuild 后同步",
            "actions": acts, "state": st2}


# ---------------- L3: 产物指纹 ----------------

def check_product_fresh(sidecar_path: str, cur_digest: str | None) -> bool | None:
    """sidecar.template_digest == 当前模板 embed digest。
    返回 True 新鲜 / False stale / None 无 digest 字段（旧产物 — 阶段 1 跳过）。"""
    try:
        sc = json.load(open(sidecar_path, encoding="utf-8"))
    except Exception:
        return None
    sd = sc.get("template_digest")
    if not sd:
        return None
    if cur_digest is None:
        return False
    return sd == cur_digest


def backfill_sidecar_dir(out_dir: str, *, log=print) -> dict:
    """阶段 2：扫 H3 output 场景目录 sidecar，无 template_digest → 补写
    （template basename → diag/ 现文件 embed digest；无历史模板 = 'unknown'）。
    幂等：已有 digest 跳过。返回 {scanned, patched, unknown}。"""
    patched = unknown = 0
    scanned = 0
    for f in sorted(os.listdir(out_dir)):
        if not re.search(r"_run\d+\.json$", f):
            continue
        p = os.path.join(out_dir, f)
        try:
            sc = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        scanned += 1
        if sc.get("template_digest"):
            continue
        tb = sc.get("template") or ""
        dig = "unknown"
        for cand in (tb, tb.replace("legacy_", ""), "legacy_" + tb):
            fp = os.path.join(SCRIPT_DIR, cand)
            if os.path.exists(fp):
                d = template_embed_digest(fp)
                if d:
                    dig = d
                    break
        sc["template_digest"] = dig
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(sc, fh, ensure_ascii=False, indent=1)
        if dig == "unknown":
            unknown += 1
        else:
            patched += 1
        log(f"[chain_check] backfill {f} → {dig}")
    return {"scanned": scanned, "patched": patched, "unknown": unknown}


# ---------------- CLI ----------------

def _add_common(ap):
    ap.add_argument("--shot", required=True)
    ap.add_argument("--md", required=True, help="源 md（项目相对路径）")
    ap.add_argument("--episodes", default=f"{SCENE}/configs/episodes.json")
    ap.add_argument("--template", action="append", required=True,
                    help="模板路径（可多次）")


def cmd_scan(args):
    """全量镜链状态（L5 pre-push/CI 用）。逐 md → chain_state（模板 = shot_templates
    全扫）。分级：blocking = 有 ≥1 模板且链断（真 driff：md 改未 rebuild / 手改冲突 —
    2b/shot_01 类）；note = 无模板镜（dormant 旧实验 md — 从未在当前链 build，不挡 CI）。"""
    bad, dormant, rows, seen, dupes = [], [], [], set(), []
    for md_rel in all_mds():
        fm = parse_fm(os.path.join(AISV_ROOT, md_rel))
        shot = fm["shot"]
        if shot in seen:
            dupes.append({"shot": shot, "dup_md": md_rel})
            continue
        seen.add(shot)
        tmpls = [t for t in shot_templates(shot) if os.path.exists(t)]
        if not tmpls:
            rows.append({"shot": shot, "md": md_rel, "templates": 0, "ok": True,
                         "broken": ["dormant（无模板 — 从未 build）"]})
            dormant.append(rows[-1])
            continue
        st = chain_state(shot, md_rel, args.episodes, tmpls)
        rows.append({"shot": shot, "md": md_rel, "templates": len(tmpls), "ok": st["ok"],
                     "broken": st["broken"] + [f"fm: {b}" for b in st["fm_drift"]]})
        if not st["ok"]:
            bad.append(rows[-1])
    print(json.dumps({"scanned": len(rows), "broken_shots": bad,
                      "dormant": [r["shot"] for r in dormant], "dup_md": dupes},
                     ensure_ascii=False, indent=1))
    return 1 if bad else 0


def cmd_check(args):
    st = chain_state(args.shot, args.md, args.episodes, args.template)
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0 if st["ok"] else 1


def cmd_reconcile(args):
    r = auto_reconcile(args.shot, args.md, args.episodes, args.template,
                       python_exe=args.python, dry=args.dry, force=args.force)
    print(json.dumps({k: v for k, v in r.items() if k != "state"},
                     ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


def cmd_backfill(args):
    print(json.dumps(backfill_sidecar_dir(args.dir), ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="链三层纯读快照")
    _add_common(p)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("reconcile", help="L1 自动同步（L2 手改不覆盖）")
    _add_common(p)
    p.add_argument("--python", default=None, help="python 可执行（缺省 sys.executable）")
    p.add_argument("--dry", action="store_true", help="只报将执行的命令")
    p.add_argument("--force", action="store_true",
                   help="人工放行：跳过 L2 fail-closed（模板出带核实后使用）")
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("backfill", help="L3 阶段 2：旧 sidecar 补 digest")
    p.add_argument("--dir", required=True, help="H3 output 场景目录（含 *_run*.json）")
    p.set_defaults(fn=cmd_backfill)
    p = sub.add_parser("scan", help="L5：全量镜链状态（pre-push/CI 用）")
    p.add_argument("--episodes", default=f"{SCENE}/configs/episodes.json")
    p.set_defaults(fn=cmd_scan)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
