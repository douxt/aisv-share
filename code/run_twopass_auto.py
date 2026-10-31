#!/usr/bin/env python3
"""run_twopass_auto.py — 两遍工作流无人值守跑批（2026-08-21）

官方版 ×3 + lorav4 版 ×3，各 15s shot_01。自主运维：
  - 每发前 RAM 守护：空闲 < 10GB → /api/free；仍不足 → 重启 H3
  - 每 3 发强制 /api/free + 重启 H3（连跑 RAM 累积铁律，两遍更重）
  - OOM/L2 错误 → /api/free + 换种子重试 1 次 → 仍败记 skip 继续
  - 超时 → 只打 timeout 标记 + 多信号分析（GPU util/power/log age），禁止 interrupt（用户 08-23 定）；例外（08-25 改）：提交内容有误（prompt/资产/参数）→ 人工有权停当前发+runner
  - 全信号静默 ≥5min → STUCK 标记 + 留待人工，仍不杀
  - 全部动作记 JSONL 日志 diag/twopass_auto_log.jsonl

仅用 stdlib（urllib/json/subprocess），conda python 运行。
"""
import hashlib
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

import chain_check  # 决定性门禁 L1-L3（fail-fast：缺模块即拒跑 — 2026-09-03 评审决定）

if not __debug__:
    raise SystemExit("本工具依赖 assert 做配置校验；请勿用 python -O 运行")

# Windows GBK 控制台打印 ✓/✗ 崩溃（2026-08-27 scene_chain 转发修复）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH
# H3_ROOT 来自 common
H3_PY = os.path.join(H3_ROOT, "python_embeded", "python.exe")
H3_MAIN = os.path.join(H3_ROOT, "ComfyUI", "main.py")
_PORT = int(json.load(open(SPEC_PATH, encoding="utf-8")).get("env", {}).get("h3_port", 8188))
URL = f"http://127.0.0.1:{_PORT}"
LOG = os.path.join(SCRIPT_DIR, "twopass_auto_log.jsonl")
EPISODES_REL = f"{SCENE}/configs/episodes.json"  # 链检查用（相对 AISV_ROOT — chain_check 参数约定）

RUNS = [
    # act6 6b2 二采（2026-09-02：用户选卡 s11720，终镜）
    ("ref2v_384p10", 11720),
]
SHOT = "act6_6b2"  # 场景 key（--shot 覆盖；多场景批量交付，2026-08-23）
RESTART_EVERY = 0  # 周期重启已取消（2026-08-27 用户定：RAM 稳定占用不高，--cache-ram 16 + sage 生效后无累积）；RAM 守护兜底保留
TIMEOUT_RUN = 90 * 60        # 单发超时参考点（实测 no_lora 16步+Pass2 4步 ~81min）；超时只打标记不杀（用户 08-23 定）
HARD_CAP = 12 * 3600         # 防死循环兜底（远超任何正常发；到点也只标记 + 汇报，仍不 interrupt）
STUCK_MIN = 5 * 60           # 全信号静默持续阈值 → 打 stuck 分析标记（仍不 interrupt，等用户/自然结束）
RAM_MIN_GB = 10              # 每发前空闲 RAM 下限
OUT_DIR = os.path.join(H3_ROOT, "ComfyUI", "output", SCENE, "twopass_test")
REQUIRED_NODES = [
    "MinimaxH3LatentUpscaler3D", "LTXVSeparateAVLatent", "LTXVConcatAVLatent",
    "ManualSigmas", "MiniMaxH3SigmaShift", "LoraLoaderModelOnly",
    "ComfyMathExpression", "ResolutionSelector", "MiniMaxH3ReferenceToVideo",
    "SamplerCustomAdvanced", "SplitSigmas",
    # T8mars 双链节点已随 E3 删包（2026-08-26）；当前全走 legacy 链（SplitSigmas+ManualSigmas）
]
# 禁链节点（2026-08-30 配置突变事故）：T8mars 节点已删包 → 模板若含 T8 节点 = 链型突变
# （build_twopass 默认曾=t8mars 生成 T8 模板 → 二采提交 400）→ preflight 硬校验模板禁含
FORBIDDEN_NODES = json.load(open(
    SPEC_PATH, encoding="utf-8")
)["forbidden_nodes"]  # spec 定案（2026-09-01 配置根治：去字面复制）


def log(level: str, msg: str, **kw):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level, "msg": msg, **kw}
    line = json.dumps(rec, ensure_ascii=False)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def http_get(path: str, timeout: int = 20):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def http_post(path: str, payload: dict | None = None, timeout: int = 60):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        raise RuntimeError(f"POST {path} -> {e.code}: {body}")


def h3_alive() -> bool:
    try:
        http_get("/system_stats", timeout=5)
        return True
    except Exception:
        return False


def query_gpu() -> dict | None:
    """nvidia-smi 单发采样 util/temp/power（卡死判定的可靠信号，替代不可靠的 log mtime）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, errors="replace", timeout=15)
        parts = [p.strip() for p in out.stdout.split(",")]
        return {"util": float(parts[0]), "temp": float(parts[1]), "power": float(parts[2])}
    except Exception:
        return None


def free_memory():
    try:
        http_post("/api/free", {"unload_models": True, "free_memory": True}, timeout=60)
        log("op", "api/free done")
    except Exception as e:
        log("warn", f"api/free failed: {e}")


def free_ram_gb() -> float:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
            capture_output=True, text=True, errors="replace", timeout=30)
        kb = float(out.stdout.strip())
        return kb / 1024 / 1024
    except Exception as e:
        log("warn", f"RAM read failed: {e}")
        return 999.0


def find_h3_pid() -> int | None:
    try:
        # errors="replace"：netstat 输出是 cp936，父进程若处 UTF-8 模式会解码崩（0xbb 事故）
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             errors="replace", timeout=30)
        for line in out.stdout.splitlines():
            if ":8188" in line and "LISTENING" in line:
                return int(line.split()[-1])
    except Exception as e:
        log("warn", f"netstat failed: {e}")
    return None


def stop_h3():
    pid = find_h3_pid()
    if pid:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"], timeout=60)
        log("op", f"H3 stopped (pid {pid})")
    for _ in range(30):
        if not h3_alive():
            return
        time.sleep(2)
    log("warn", "H3 still alive after stop")


def shot_duration(shot: str) -> float:
    """episodes.json 的 shot duration（sage 决策用）。"""
    try:
        src = json.load(open(os.path.join(SCENE_DIR, "configs", "episodes.json"),
                             encoding="utf-8"))
        dur = src.get("shots", src).get(shot, {}).get("duration")
        return float(dur) if dur else 10.0
    except Exception:
        return 10.0


def sage_required() -> bool:
    """RUNS 全 ≤10s → 期望 sage（≤10s 已验证安全）；含 >10s → 禁 sage（15s 花屏铁律）。"""
    if not RUNS:
        return False
    return all(shot_duration(SHOT) <= 10.0 for _ in RUNS)


def h3_cmdline() -> str:
    try:
        pid = find_h3_pid()
        if not pid:
            return ""
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return ""


def check_video_ref_frames() -> bool:
    """ref_videos 输入精确值校验（2026-09-01 配置根治：下限 ≥5 被当标准用事故）。
    模板含 VHS_LoadVideo（节点 960）→ ffprobe 帧数 == spec.video_ref_frames 才过；
    锁帧镜（含 tail_frame 图）缺 VHS = video_ref_policy 未执行 = 中止（提示 relay.py）。"""
    try:
        spec = json.load(open(SPEC_PATH,
                              encoding="utf-8"))
        expected = int(spec.get("video_ref_frames", 24))
        wf = json.load(open(template_path(RUNS[0][0]), encoding="utf-8"))
        n = wf.get("960", {})
        video = n.get("inputs", {}).get("video") if n.get("class_type") == "VHS_LoadVideo" else None
        ref0 = wf.get("9", {}).get("inputs", {}).get("image", "") if "9" in wf else ""
        is_lock_shot = "tail_frame" in ref0 or "futa_end_frame" in ref0
        if not video:
            if is_lock_shot:
                log("error", f"preflight: 锁帧镜缺 video_ref（spec.video_ref_policy 必带 "
                             f"{expected} 帧）— 用 diag/relay.py --shot {SHOT} 截取后重建模板")
                return False
            return True  # 非锁帧镜（独立镜/平行剪辑）→ 跳过
        h3_in = os.path.join(H3_ROOT, "ComfyUI", "input", video.replace("/", os.sep))
        if not os.path.exists(h3_in):
            log("error", f"preflight: video_ref 缺失 {video}（relay.py 同步过？）— abort")
            return False
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_frames", "-of", "csv", h3_in],
                           capture_output=True, text=True)
        nf = int(r.stdout.strip().split(",")[-1])
        if nf != expected:
            log("error", f"preflight: video_ref 帧数 {nf} != spec 规定 {expected} — "
                         f"用 diag/relay.py --shot {SHOT} 重截（tail24 名副其实）")
            return False
        log("op", f"preflight: video_ref {video} = {nf} 帧 ✓（spec 规定 {expected}）")
        return True
    except Exception as e:
        log("error", f"preflight: video_ref 帧数校验异常（按失败处理）: {e}")
        return False


def check_sage_flag() -> bool:
    """H3 启动 flag 自检（2026-08-27：抽卡/二采慢 1.6× 教训 — 重启后漏 sage flag）。
    期望 sage 缺 flag = 慢跑白费 GPU；禁 sage 带 flag = 15s 花屏风险。不符 = 中止。"""
    need = sage_required()
    has = "--use-sage-attention" in h3_cmdline()
    if need and not has:
        log("error", "H3 缺 --use-sage-attention（本批全 ≤10s，sage 安全可加速）— "
                     "重启 H3 带全 flag（--disable-pinned-memory --cache-ram 16 --use-sage-attention）后重跑")
        return False
    if not need and has:
        # 2026-08-30 用户授权测试：384p 新规格下 15s + sage 是否花屏（低分辨率可能无累积误差）
        if os.environ.get("AISV_SAGE_TEST") == "1":
            log("warn", "AISV_SAGE_TEST=1 — 带 sage 跑 >10s 镜（384p 规格测试，花屏风险自担）")
            return True
        log("error", "H3 带 --use-sage-attention 但本批含 >10s 镜（sage 长序列花屏铁律）— "
                     "重启 H3 去 sage flag 后重跑")
        return False
    log("op", f"H3 sage flag {'present' if has else 'absent'} (expected "
              f"{'sage' if need else 'no-sage'}, {SHOT} {shot_duration(SHOT)}s)")
    return True


def start_h3():
    cmd = [H3_PY, "-s", H3_MAIN, "--windows-standalone-build",
           "--disable-pinned-memory", "--cache-ram", "16"]
    if sage_required():
        cmd.append("--use-sage-attention")  # 仅 ≤10s 批次自动带（8-26 花屏铁律）
    log("op", "starting H3 (flags: " + " ".join(cmd[3:]) + ")")
    # CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB：尽量脱离进程树，
    # 避免 runner 被杀时连带杀 H3（2026-08-22 实测 TaskStop 连坐丢失 fl2v s4001）
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000
    subprocess.Popen(
        cmd,
        cwd=H3_ROOT,
        stdout=open(os.path.join(H3_ROOT, "comfyui_twopass_auto.log"), "a"),
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    for i in range(48):  # 最多 8 min
        if h3_alive():
            log("op", f"H3 up after ~{i * 10}s")
            return True
        time.sleep(10)
    log("error", "H3 failed to start in 8 min")
    return False


def restart_h3():
    log("op", "=== H3 restart ===")
    stop_h3()
    time.sleep(10)
    return start_h3()


def _md5_file(p: str) -> str:
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_paths_in(wf: dict) -> list[str]:
    """提取模板引用的全部资产相对路径（LoadImage.image / LoadAudio.audio）。"""
    paths = []
    for n in wf.values():
        ct = n.get("class_type", "")
        if ct in ("LoadImage", "LoadAudio"):
            v = n.get("inputs", {}).get("image" if ct == "LoadImage" else "audio")
            if isinstance(v, str):
                paths.append(v.replace("\\", "/"))
    return paths


def check_asset_consistency(paths: list[str]) -> list[str]:
    """项目份 vs H3 份 md5 一致性。返回问题列表（空 = 全一致）。

    双份机制（2026-08-24 教训）：junction 被 H3 拒绝 → 资产 = 真实目录双份。
    只改项目份 = H3 继续用旧文件（futa_end_frame 首帧事故）。"""
    h3root = os.path.join(H3_ROOT, "ComfyUI", "input")
    projroot = os.path.join(SCENE_DIR)
    issues = []
    for rel in paths:
        proj_rel = rel.replace("/", os.sep)
        if proj_rel.startswith("magic" + os.sep):  # 模板 rel = magic/assets/... → 项目根 = magic/
            proj_rel = proj_rel[len("magic" + os.sep):]
        pp = os.path.join(projroot, proj_rel)
        hp = os.path.join(h3root, rel.replace("/", os.sep))
        if not os.path.exists(pp):
            issues.append(f"资产不存在(项目): {rel}")
            continue
        if not os.path.exists(hp):
            issues.append(f"资产缺失(H3 input): {rel} — 跑 python diag/sync_assets.py --sync")
            continue
        if _md5_file(pp) != _md5_file(hp):
            issues.append(f"资产双份不一致: {rel} — 跑 python diag/sync_assets.py --sync")
    return issues


def preflight() -> bool:
    """节点注册 + 模型文件 + 模板静态校验。"""
    if not h3_alive():
        log("error", "H3 offline at preflight")
        return False
    try:
        for t in REQUIRED_NODES:
            info = http_get(f"/object_info/{t}", timeout=30)
            if t not in info:
                log("error", f"node NOT registered: {t}")
                return False
        log("op", "preflight: all required nodes registered")
        # 模板规格硬校验（稳定律 2026-08-30：链型 + 分辨率 vs twopass_spec.json 唯一事实源）
        spec = json.load(open(SPEC_PATH, encoding="utf-8"))
        for variant, _ in RUNS:
            tmpl = json.load(open(template_path(variant), encoding="utf-8"))
            bad = [k for k in tmpl if tmpl[k]["class_type"] in FORBIDDEN_NODES]
            if bad:
                log("error", f"preflight: 模板 {template_path(variant)} 含禁链节点 {bad} "
                             f"— 链型突变！重建模板（python diag/build_twopass.py --shot {SHOT}）")
                return False
            mp = tmpl.get("20", {}).get("inputs", {}).get("megapixels")
            if mp != spec["pass1_mp"]:
                log("error", f"preflight: 模板 {template_path(variant)} 分辨率 {mp}MP != 规格 "
                             f"{spec['pass1_mp']}MP（{spec['pass1_resolution']} 定案）— 重建模板")
                return False
        log("op", f"preflight: template spec OK (chain={spec['chain']}, "
                  f"{spec['pass1_mp']}MP {spec['pass1_resolution']})")
        # 放大模型是否被节点扫描到
        info = http_get("/object_info/MinimaxH3LatentUpscaler3D", timeout=30)
        models = json.dumps(info["MinimaxH3LatentUpscaler3D"])
        assert "minimax_h3_latent_upscaler_3d_fp16.safetensors" in models, \
            "upscaler model not in node's model list"
        log("op", "preflight: upscaler fp16 model listed")
    except Exception as e:
        log("error", f"preflight failed: {e}")
        return False
    # 模板新鲜度预检（2026-09-03 chain_check 挂载：删除旧内联 STALE 块 — 双实现消除 + 硬编码
    # --pass1-mp/--label 残留删除。三层比对 = md text 块 vs episodes prompt vs 模板 embed + fm 漂移）
    md_rel = chain_check.resolve_md(SHOT)
    templates = [template_path(v) for v, _ in RUNS]
    if not md_rel:
        log("op", f"preflight: {SHOT} 无源 md（voice_extract 等临时场景）— 链检查跳过")
    else:
        cs = chain_check.chain_state(SHOT, md_rel, EPISODES_REL, templates)
        if not cs["ok"]:
            log("error", f"preflight: 链不一致 — broken={cs['broken']} "
                         f"fm_drift={cs['fm_drift']}。{cs['fix_cmd']}")
            return False
        log("op", f"preflight: chain 三层同步 ✓（{SHOT}）")
    # 模板静态预检（教训固化：payload 预检分辨率）
    for variant, _ in RUNS:
        p = template_path(variant)  # 多场景带 SHOT 前缀（2026-08-23 教训：裸拼读到 shot_01 遗留旧模板）
        wf = json.load(open(p, encoding="utf-8"))
        assert abs(wf["20"]["inputs"]["megapixels"] - spec["pass1_mp"]) < 1e-3, \
            f"{variant}: Pass1 MP {wf['20']['inputs']['megapixels']} != spec {spec['pass1_mp']}"
        if "26" in wf:  # twopass 变体有放大节点
            assert wf["26"]["inputs"]["mode"] == "scale by multiplier", f"{variant}: upscale mode"
            # 稳定律：scale = spec 定案（2026-08-30 384p 规格 1.8333；历史 1.5/2.0 兼容）
            assert abs(wf["26"]["inputs"]["mode.scale"] - spec["upscale_scale"]) < 1e-3 or \
                wf["26"]["inputs"]["mode.scale"] in (1.5, 2.0), \
                f"{variant}: scale != spec {spec['upscale_scale']}/1.5/2.0"
            assert wf["26"]["inputs"]["align"] == 32, f"{variant}: align != 32"
        if "33" in wf:  # 旧 twopass 变体有 Pass2 ManualSigmas
            assert wf["33"]["inputs"]["sigmas"] == "0.9035, 0.8000, 0.6316, 0.3158, 0.0000", \
                f"{variant}: Pass2 sigmas wrong"
        if "13" in wf:  # 常规模板有 SplitSigmas（Turbo 版直连 BasicScheduler 无 13）
            assert wf["13"]["inputs"]["step"] in (4, 6, 8, 16), f"{variant}: Pass1 steps weird"
        elif "24" in wf:
            assert wf["24"]["inputs"]["steps"] in (4, 6, 8, 16), f"{variant}: Turbo steps weird"
        elif "40" in wf:  # T8mars 8-21 结构（2026-08-25）：Parity Plan coarse 4 步
            assert wf["40"]["inputs"]["coarse_steps"] == 4, f"{variant}: T8 coarse_steps weird"
        # 引用完整性
        ids = set(wf.keys())
        for nid, n in wf.items():
            for k, v in n["inputs"].items():
                if isinstance(v, list) and len(v) == 2:
                    assert v[0] in ids, f"{variant} #{nid}.{k} refs missing {v[0]}"
        # 资产双份一致性（2026-08-24 教训：只改项目份 = H3 用旧 ref）
        issues = check_asset_consistency(asset_paths_in(wf))
        if issues:
            for i in issues:
                log("error", f"preflight: {variant} {i}")
            log("error", "preflight: 资产不一致 — 先 python diag/sync_assets.py --sync 再跑")
            return False
        log("op", f"preflight: {variant} template OK ({len(wf)} nodes, assets consistent)")
    return True


def classify_error(history: dict) -> str | None:
    """从 history 提取错误消息；OOM 类返回 'oom'，其他 'error'，无错误 None。"""
    try:
        status = history["status"]["status_str"]
    except (KeyError, TypeError):
        return None
    if status != "error":
        return None
    msgs = history["status"].get("messages", [])
    text = json.dumps(msgs, ensure_ascii=False).lower()
    for m in msgs:
        if isinstance(m, list) and m and m[0] == "execution_error":
            log("error", "node error: " + json.dumps(m[1], ensure_ascii=False)[:400])
    if "out of memory" in text or "oom" in text or "cuda memory" in text:
        return "oom"
    return "error"


def check_ref_desc_sync(confirm: bool = False) -> bool:
    """跑前 ref 锁帧描述同步检查（2026-08-26 摸头事故机制补缺）。

    接力 ref（上镜产物末帧）≠ 设计意图 → 下镜 prompt 首帧描述若未按新末帧
    重写 = 模型按旧描述重构首帧 = 接缝断裂。

    2026-08-26 实现：VLM 自动比对（check_ref_desc.py + Qwen3-VL-4B GPU ~13s）。
      - VLM 一致 → 通过；不一致 → 中止（重写描述）
      - VLM 内部失败 → 降级人工确认（--confirm-ref-desc，机制未挂死）
    """
    wf = json.load(open(template_path(RUNS[0][0]), encoding="utf-8"))
    ref0 = wf["9"]["inputs"]["image"] if "9" in wf else None
    if not ref0 or not any(k in ref0 for k in ("futa_end_frame", "tail_frame")):
        return True  # 非接力 ref（首镜/独立场景）→ 跳过
    # 构图预检（2026-09-03，2c 事故补缺）：md↔md 纯文本零 GPU — 下游 [Shot 1] 复述
    # framing 与上游 md 尾段设计强对立（wide↔tight）→ 锁帧必败，VLM 前即拦
    try:
        import check_lockframe_desc as lf
        up_shot = os.path.basename(ref0).replace("_tail_frame.png", "").replace("_end_frame.png", "")
        up_md = chain_check.resolve_md(up_shot) if up_shot else None
        md_rel = chain_check.resolve_md(SHOT)
        if up_md and md_rel:
            lf.AISV_ROOT = AISV_ROOT
            r = lf.pair_check(up_md, md_rel, up_shot, SHOT)
            if r.get("verdict") == "CONFLICT":
                log("error", f"preflight: 锁帧构图矛盾（2c 事故类）— 下游 [Shot 1] 复述 "
                             f"{r['down_framing']} vs 上游 {up_shot} 尾段设计 "
                             f"{r['up_framing']}（{r['up_class']}↔{r['down_class']} 强对立）"
                             f"→ 锁帧必败。改下游首帧尺度复述或去锁帧 "
                             f"（python diag/check_lockframe_desc.py pair --up {up_md} "
                             f"--down {md_rel}）")
                return False
            if r.get("verdict") == "OK":
                log("op", f"preflight: 锁帧构图预检 ✓（{up_shot}→{SHOT} "
                          f"{r['up_class']}↔{r['down_class']}）")
    except Exception as e:
        log("warn", f"preflight: 构图预检异常（跳过不拦）: {e}")
    ref_abs = os.path.join(AISV_ROOT, ref0.replace("/", os.sep))
    tmpl_abs = template_path(RUNS[0][0])
    log("op", f"preflight: 接力 ref {ref0} — VLM 自动比对（Qwen3-VL-4B，~15s）...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    _chk = os.path.join(SCRIPT_DIR, "check_ref_desc.py")
    if not os.path.exists(_chk):
        log("warn", "preflight: check_ref_desc.py 未随包 — 跳过 VLM 锁帧比对（需人工确认 ref 描述）")
        return True
    r = subprocess.run([sys.executable, _chk,
                        "--ref", ref_abs, "--template", tmpl_abs],
                       capture_output=True, text=True, encoding="utf-8", timeout=900, env=env)
    out = (r.stdout + r.stderr).strip()
    for line in out.splitlines():
        log("op", f"check_ref_desc: {line}")
    if r.returncode == 0:
        if "描述一致" in out:
            log("op", "preflight: ref 描述同步 ✓（VLM 自动比对通过）")
            return True
        # VLM 内部失败降级（exit 0 + 人工校验提示）→ 走人工确认门
        if not confirm:
            log("error", "preflight: VLM 自动比对不可用 — 人工对照 ref 图与模板描述后 "
                         "加 --confirm-ref-desc 重跑")
            return False
        return True
    log("error", "preflight: ref 描述与模板不一致（VLM 判定）— 重写首帧描述后重跑 "
                 "(check_ref_desc.py 详情见上)")
    return False


def scene_chain_check() -> bool:
    """场景状态继承检查（2026-08-26 L2）：上镜尾帧角色去向 + scene_inherit 必填。
    通过 check_scene_chain.py；fail（scene_inherit 缺失）= 设计未完成，中止。"""
    log("op", f"preflight: 场景链检查（{SHOT}，上镜自动推导）...")
    _chk = os.path.join(SCRIPT_DIR, "check_scene_chain.py")
    if not os.path.exists(_chk):
        log("warn", "preflight: check_scene_chain.py 未随包 — 跳过场景链检查")
        return True
    r = subprocess.run([sys.executable, _chk,
                        SHOT], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=="):
            continue
        if line.startswith("  ✗"):
            log("error", f"scene_chain: {line.strip()}")
        elif line.startswith("  ⚠"):
            log("warn", f"scene_chain: {line.strip()}")
        else:
            log("op", f"scene_chain: {line.strip()}")
    if r.returncode != 0:
        log("error", "preflight: 场景链检查失败 — scene_inherit 缺失/断裂。补齐设计后重跑")
        return False
    log("op", "preflight: 场景状态继承 ✓")
    return True


def c_layer_check() -> bool:
    """C 层 VLM 语义审查（prompt_review.py，2026-08-27 固化为抽卡前置）。
    context 自动取源 md frontmatter scene_inherit（v2.1 教训：无剧情上下文 = 漏检）。
    WARN 级不门禁（PQSG：VLM 对 actions ~63% 可靠）— 发现项入日志给人过目。"""
    md = None
    for p in glob.glob(os.path.join(SCENE_DIR, "docs", "prompts", "**", "*.md"),
                       recursive=True):
        txt = open(p, encoding="utf-8").read()
        if re.search(rf"^shot:\s*{re.escape(SHOT)}\s*$", txt, re.M):
            md = p
            break
    if md is None:
        log("warn", "C 层审查: 找不到源 md，跳过")
        return True
    txt = open(md, encoding="utf-8").read()
    m = re.search(r'scene_inherit:\s*"?(.*?)"?\n', txt)
    ctx = m.group(1) if m else ""
    log("op", f"preflight: C 层 VLM 语义审查（prompt_review {SHOT}，~40s）...")
    _chk = os.path.join(SCRIPT_DIR, "prompt_review.py")
    if not os.path.exists(_chk):
        log("warn", "preflight: prompt_review.py 未随包 — 跳过 C 层语义审查")
        return True
    r = subprocess.run([sys.executable, _chk,
                        md, "--context", ctx],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            log("warn", f"C 层发现: {line}")
        elif "NO_ISSUES" in line.upper():
            log("op", "C 层审查 ✓ NO_ISSUES")
        else:
            log("op", f"C 层: {line}")  # 08-27：VLM 输出全文进日志（判定 bug 诊断用）
    if r.returncode == 1:
        log("warn", "C 层审查: 有发现（WARN 级，见上）— 人工过目后确认继续")
    elif r.returncode == 2:
        log("warn", "C 层审查: 输出异常（见上全文）— 跳过，人工过目")
    return True


def prompt_validate() -> bool:
    """H3 prompt 机械+语义校验（validate_h3_prompt.py，2026-08-26 接入例行）。
    源 md（frontmatter shot 匹配）→ 校验；ERROR = 中止，WARN = 提示。"""
    try:
        src = json.load(open(os.path.join(SCENE_DIR, "configs", "episodes.json"),
                             encoding="utf-8"))
        dur = src.get("shots", src).get(SHOT, {}).get("duration", 15.0)
    except Exception:
        dur = 15.0
    # 找源 md（glob frontmatter shot 匹配）
    md = None
    for p in glob.glob(os.path.join(SCENE_DIR, "docs", "prompts", "**", "*.md"),
                       recursive=True):
        txt = open(p, encoding="utf-8").read()
        if re.search(rf"^shot:\s*{re.escape(SHOT)}\s*$", txt, re.M):
            md = p
            break
    if md is None:
        log("warn", f"prompt_validate: 找不到 {SHOT} 源 md，跳过")
        return True
    log("op", f"preflight: prompt 校验（validate_h3_prompt {SHOT}）...")
    # 2026-08-27: 路径修正 — validate 真身位于 .agents/skills（.claude 无此文件，FileNotFoundError 隐患）
    val_script = os.path.join(AISV_ROOT, ".agents", "skills",
                              "h3-prompt-writing", "scripts", "validate_h3_prompt.py")
    if not os.path.exists(val_script):
        val_script = os.path.join(AISV_ROOT, ".claude", "skills",
                                  "h3-prompt-writing", "scripts", "validate_h3_prompt.py")
    if not os.path.exists(val_script):
        val_script = os.path.join(SCRIPT_DIR, "validate_h3_prompt.py")
    if not os.path.exists(val_script):
        log("warn", "preflight: validate_h3_prompt.py 未随包 — 跳过结构校验")
        return True
    r = subprocess.run([sys.executable, val_script, md, "--duration", str(dur)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or "[info]" in line:
            continue
        if line.startswith("[ERROR]"):
            log("error", f"prompt_validate: {line}")
        elif line.startswith("[WARN]"):
            log("warn", f"prompt_validate: {line}")
    if r.returncode == 1:
        log("error", "preflight: prompt 校验有 ERROR — 修正后重跑")
        return False
    if r.returncode not in (0, 1):
        log("error", f"preflight: prompt 校验器异常退出 {r.returncode}")
        return False
    log("op", "preflight: prompt 校验 ✓")
    return True


def template_path(variant: str) -> str:
    """模板文件：shot_01 保持历史名 api_workflow_twopass_{variant}.json；多场景带前缀。
    2026-08-31：build_twopass legacy 链产出带 legacy_ 前缀（c5a7fd8 命名）→ 优先 legacy_，
    回退无前缀（act4 轮历史模板无前缀）。"""
    stem = variant if SHOT == "shot_01" else f"{SHOT}_{variant}"
    candidates = [
        os.path.join(SCRIPT_DIR, f"api_workflow_twopass_legacy_{stem}.json"),
        os.path.join(SCRIPT_DIR, f"api_workflow_twopass_{stem}.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]  # 都不存在 → 返回 legacy 路径（preflight 报错信息更准确）


def out_base(variant: str) -> tuple[str, str]:
    """产物位置 → (输出目录, 文件名前缀)。
    目录 = 模板前缀的 dirname（场景目录：twopass_test / act2_2a），
    文件名 = RUNS 变体名（含 label 如 fl2v11_draw_480p15，与历史产物一致）。"""
    wf = json.load(open(template_path(variant), encoding="utf-8"))
    base = wf["5"]["inputs"]["filename_prefix"]  # magic/twopass_test/fl2v11_draw
    rel_dir = os.path.dirname(base)
    out_dir = os.path.join(H3_ROOT, "ComfyUI", "output", rel_dir)
    return out_dir, variant


def output_exists(variant: str, seed: int) -> bool:
    """断点续跑：SaveVideo 产物已存在则跳过。
    2026-09-01 配置根治：存在 + ffprobe 内容校验（分辨率 == spec.deliver_resolution）
    双条件 — 脏产物（截尾/错分辨率）不再算成功。"""
    out_dir, prefix = out_base(variant)
    prefix = f"{prefix}_run{seed}"
    try:
        names = os.listdir(out_dir)
    except FileNotFoundError:
        return False
    # 2026-08-25 修正：只认 mp4 产物（sidecar json 同前缀 = 历史记录，不算产物）
    mp4s = [n for n in names if n.startswith(prefix) and n.endswith(".mp4")]
    if not mp4s:
        return False
    try:
        spec = json.load(open(SPEC_PATH,
                              encoding="utf-8"))
        key = "pass1_resolution" if "draw" in variant else "deliver_resolution"
        exp_w, exp_h = spec[key].lower().split("x")
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            os.path.join(out_dir, mp4s[0])],
                           capture_output=True, text=True)
        w, h = r.stdout.strip().split(",")[:2]  # -of csv=p=0 去 "stream," 前缀
        if w != exp_w or h != exp_h:
            log("error", f"产物 {mp4s[0]} 分辨率 {w}x{h} != spec 交付 "
                         f"{spec['deliver_resolution']} — 不视为产物（重跑）")
            return False
    except Exception as e:
        log("error", f"产物分辨率校验异常（按不存在算）: {e}")
        return False
    return True


def wait_queue_drain(timeout: int = 3 * 3600):
    """等 ComfyUI 队列排空（续跑时避免与残留任务叠加）。最长单发 ~59min，3h 兜底。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            q = http_get("/queue", timeout=10)
            if not q.get("queue_running"):
                return True
            log("op", "queue busy, waiting drain...")
        except Exception:
            pass
        time.sleep(20)
    log("error", "queue did not drain in 3h")
    return False


def write_sidecar(variant: str, seed: int, wf: dict, result: dict):
    """产物旁写同前缀 .json：seed + 全参数快照，供后续选卡放大复现。"""
    side = {
        "variant": variant,
        "seed": seed,
        "template": os.path.basename(template_path(variant)),
        # L3 产物指纹（2026-09-03）：用什么图纸造的 — quality gate 前验 == 当前模板
        "template_digest": chain_check.template_embed_digest(template_path(variant)),
        "pass1": {
            "megapixels": wf["20"]["inputs"]["megapixels"],
            "aspect_ratio": wf["20"]["inputs"]["aspect_ratio"],
            "iters": (wf["13"]["inputs"]["step"] if "13" in wf
                      else wf["24"]["inputs"]["steps"] if "24" in wf
                      else wf["40"]["inputs"]["coarse_steps"]),
            "scheduler_steps": (wf["24"]["inputs"]["steps"] if "24" in wf
                                else wf["40"]["inputs"]["base_steps"]),
            "sampler": wf["18"]["inputs"]["sampler_name"] if "18" in wf else "turbo",
            "sigma_shift": ((wf["38"]["inputs"]["shift_video"], wf["38"]["inputs"]["shift_audio"])
                            if "38" in wf else None),
            "lora": wf.get("36", {}).get("inputs", {}).get("lora_name", None)
                    or wf.get("200", {}).get("inputs", {}).get("lora_name", None),
            "strength": wf.get("36", {}).get("inputs", {}).get("strength_model", None)
                        or wf.get("200", {}).get("inputs", {}).get("strength", None),
            "unet": wf["30"]["inputs"]["unet_name"],
        },
        "upscale": {
            "mode": wf.get("26", {}).get("inputs", {}).get("mode", None),
            "scale": wf.get("26", {}).get("inputs", {}).get("mode.scale", None),
            "align": wf.get("26", {}).get("inputs", {}).get("align", None),
            "model": wf.get("26", {}).get("inputs", {}).get("model_name", None),
        },
        "pass2": wf.get("33", {}).get("inputs", {}).get("sigmas", None),
        # 资产快照（2026-08-24：产物所用 ref 的可追溯性 — 双份断裂事故的证伪工具）
        "ref_assets": {rel: _md5_file(os.path.join(SCENE_DIR,
                                                   rel.replace(SCENE + "/", "", 1).replace("/", os.sep)))
                       for rel in asset_paths_in(wf)},
        "result": {k: v for k, v in result.items() if k not in ("outputs",)},
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        out_dir, _ = out_base(variant)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{variant}_run{seed}.json"), "w", encoding="utf-8") as f:
            json.dump(side, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log("warn", f"sidecar write failed: {e}")


def run_one(variant: str, seed: int) -> dict:
    if output_exists(variant, seed):
        log("op", f"skip [{variant} s{seed}] — output exists (resume)")
        # L3 阶段 1：旧产物（sidecar 无 template_digest）不拦 — backfill 后启用全量 frozen
        try:
            out_dir, prefix = out_base(variant)
            for sc_name in os.listdir(out_dir):
                if sc_name.startswith(f"{prefix}_run{seed}") and sc_name.endswith(".json"):
                    sc = json.load(open(os.path.join(out_dir, sc_name), encoding="utf-8"))
                    if not sc.get("template_digest"):
                        log("op", f"skip [{variant} s{seed}] sidecar 无 template_digest "
                                  f"（旧产物 — L3 阶段 1 跳过；可 chain_check backfill 启用）")
                    break
        except Exception:
            pass
        return {"variant": variant, "seed": seed, "result": "skipped_existing"}
    # L1 使用点自动同步（2026-09-03 决定性门禁）：模板使用前链检查 — md/模板 stale → 自动
    # rebuild；手改冲突（L2）→ fail-closed 中止。人工直跑与 auto_pipeline 子进程同汇于此。
    md_rel = chain_check.resolve_md(SHOT)
    templates = [template_path(v) for v, _ in RUNS]
    if md_rel:
        rc = chain_check.auto_reconcile(SHOT, md_rel, EPISODES_REL, templates, log=log)
        if not rc["ok"]:
            log("error", f"chain reconcile 失败 [{variant} s{seed}]: {rc['reason']}")
            return {"variant": variant, "seed": seed, "result": "chain_reconcile_fail",
                    "reason": rc["reason"], "actions": rc.get("actions", [])}
        if rc.get("actions"):
            log("op", f"[{variant} s{seed}] L1 auto-reconcile 已自动 rebuild: "
                      f"{'; '.join(rc['actions'])}")
    wf = json.load(open(template_path(variant), encoding="utf-8"))
    wf["31"]["inputs"]["noise_seed"] = seed
    # 前缀 = 场景目录（模板前缀 dirname）+ {variant}_run{seed}（历史命名不变）
    scene_dir = os.path.dirname(wf["5"]["inputs"]["filename_prefix"])
    wf["5"]["inputs"]["filename_prefix"] = f"{scene_dir}/{variant}_run{seed}"

    t0 = time.time()
    try:
        result = http_post("/prompt", {"prompt": wf}, timeout=120)
    except Exception as e:
        log("error", f"submit failed [{variant} s{seed}]: {e}")
        return {"variant": variant, "seed": seed, "result": "submit_error", "secs": time.time() - t0}
    if "node_errors" in result and result.get("node_errors"):
        log("error", f"node_errors [{variant} s{seed}]: "
                     f"{json.dumps(result['node_errors'], ensure_ascii=False)[:400]}")
        return {"variant": variant, "seed": seed, "result": "node_error", "secs": time.time() - t0}
    pid = result.get("prompt_id")
    if not pid:
        log("error", f"no prompt_id [{variant} s{seed}]: {str(result)[:200]}")
        return {"variant": variant, "seed": seed, "result": "no_prompt_id", "secs": time.time() - t0}

    log("op", f"submitted [{variant} s{seed}] pid={pid}")
    timed_out = False
    stuck_since = None
    H3_LOG = os.path.join(H3_ROOT, "comfyui_twopass_auto.log")
    while True:
        time.sleep(20)
        if time.time() - t0 >= HARD_CAP:
            log("error", f"HARD_CAP {HARD_CAP / 3600:.0f}h reached [{variant} s{seed}] — "
                         f"标记不杀，继续等 queue 自然结束或用户决策")
            timed_out = True
        try:
            hist = http_get(f"/history/{pid}", timeout=30)
        except Exception:
            continue
        if pid not in hist:
            continue
        err = classify_error(hist[pid])
        if err:
            return {"variant": variant, "seed": seed, "result": err,
                    "secs": time.time() - t0, "prompt_id": pid}
        if "outputs" in hist[pid]:
            outs = []
            for nid, o in hist[pid]["outputs"].items():
                for k in ("gifs", "videos"):
                    for item in o.get(k, []):
                        outs.append(item)
            result = {"variant": variant, "seed": seed, "result": "ok",
                      "secs": time.time() - t0, "outputs": outs, "prompt_id": pid,
                      **({"timeout_marker": True} if timed_out else {})}
            write_sidecar(variant, seed, wf, result)
            tag = " (超时标记)" if timed_out else ""
            log("ok", f"done [{variant} s{seed}] in {(time.time() - t0) / 60:.1f} min -> {outs}{tag}")
            return result
        # 超时标记（只记一次，不杀）
        if not timed_out and time.time() - t0 >= TIMEOUT_RUN:
            timed_out = True
            log("warn", f"timeout {TIMEOUT_RUN / 60:.0f}min marker [{variant} s{seed}] — "
                        f"继续等待不中断（用户 08-23 定：禁止超时杀死）")
        if timed_out:
            # 分析模式：多信号判定（GPU util/power + log mtime + queue），不中断
            gpu = query_gpu()
            try:
                mtime = os.path.getmtime(H3_LOG)
                log_age = time.time() - mtime
            except OSError:
                log_age = 0  # log 不可读 → 假设活跃
            alive = (gpu is None) or (gpu["util"] > 10 or gpu["power"] > 40) or log_age < 300
            if alive:
                stuck_since = None
                g = f"{gpu['util']:.0f}%/{gpu['temp']:.0f}C/{gpu['power']:.0f}W" if gpu else "n/a"
                log("op", f"analysis: alive (gpu {g}, log_age {log_age:.0f}s), "
                          f"elapsed {(time.time() - t0) / 60:.0f}min, keep waiting")
            else:
                if stuck_since is None:
                    stuck_since = time.time()
                stuck_for = time.time() - stuck_since
                log("warn", f"analysis: idle {stuck_for / 60:.1f}min (gpu {gpu}, log_age {log_age:.0f}s) "
                            f"— 标记不杀，等用户决策/自然结束")
                if stuck_for >= STUCK_MIN:
                    log("error", f"STUCK MARKER [{variant} s{seed}] 全信号静默 {STUCK_MIN / 60:.0f}min — "
                                 f"不 interrupt（误判教训 2.0× 案例），等待人工处理")


def status_report() -> int:
    """--status：新会话一键恢复报告（2026-08-31 上下文压缩事故后的启动协议）。
    全派生无状态文件：生效配置摘要/当前批/产物存在性/relay 链帧数/待办/最近提交/H3。"""
    def _hdr(t):
        print(f"\n== {t} ==")

    print(f"H3 {'ALIVE' if h3_alive() else 'OFFLINE'} @8188 | sage flag: "
          f"{'ok' if (sage_required() == ('--use-sage-attention' in h3_cmdline())) else 'MISMATCH'}")
    _hdr("生效配置（twopass_spec.json）")
    try:
        spec = json.load(open(SPEC_PATH,
                              encoding="utf-8"))
        print(f"  rev {spec['revision']} | chain={spec['chain']} | "
              f"抽卡 {spec['pass1_mp']}MP {spec['pass1_resolution']} | "
              f"二采 x{spec['upscale_scale']} → {spec['deliver_resolution']}")
        print(f"  label={spec['label']} | sampler={spec['sampler']} "
              f"scheduler={spec['scheduler']} | video_ref={spec['video_ref_frames']}帧"
              f"(relay.py) | xfade={spec['seam_xfade_sec']}s(seam_join.py)")
    except Exception as e:
        print(f"  (spec 不可读: {e})")
    _hdr("当前批")
    print(f"SHOT = {SHOT}（{shot_duration(SHOT)}s，sage={'需要' if sage_required() else '禁止'}）")
    for variant, seed in RUNS:
        try:
            ok = output_exists(variant, seed)
        except Exception as e:
            ok = False
            print(f"  (模板/产物不可用: {e})")
        print(f"  {variant} s{seed}: {'产物已存在 → 续跑跳过' if ok else '待跑'}")
    _hdr("种子历史（最近 8 次 submitted）")
    try:
        import json as _j
        lines = [l for l in open(LOG, encoding="utf-8") if "submitted" in l][-8:]
        for l in lines:
            d = _j.loads(l)
            print(f"  {d['ts'][11:19]} {d['msg']}")
    except Exception as e:
        print(f"  (log 不可读: {e})")
    _hdr("relay 链（tail_frame + tail24 帧数）")
    shots_dir = os.path.join(SCENE_DIR, "assets", "shots", "shot_battle")
    if not os.path.isdir(shots_dir):
        print("  (目录不存在 — 先跑 relay.py 生成尾帧)")
    for f in sorted(os.listdir(shots_dir)) if os.path.isdir(shots_dir) else []:
        if f.endswith("_tail_frame.png"):
            v24 = f.replace("_tail_frame.png", "_tail24.mp4")
            p24 = os.path.join(shots_dir, v24)
            n = "MISSING"
            if os.path.exists(p24):
                import subprocess as _sp
                r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=nb_frames", "-of", "csv", p24],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
                n = r.stdout.strip().split(",")[-1] + "帧"
            print(f"  {f[:-4]}: frame ✓ / {v24} {n}")
    _hdr("待办（episodes 顺序 > 当前 SHOT）")
    try:
        ep = json.load(open(os.path.join(SCENE_DIR, "configs", "episodes.json"),
                            encoding="utf-8"))
        shots = ep.get("shots", ep)
        cur = (ep.get("shots", {}).get(SHOT, {}) or {}).get("order", 0) if isinstance(
            ep.get("shots"), dict) else 0
        pending = sorted(
            [(s, v.get("order", 99)) for s, v in (shots.items() if isinstance(shots, dict) else [])],
            key=lambda x: x[1])
        print("  " + " → ".join(s for s, o in pending if o > cur))
    except Exception as e:
        print(f"  (episodes 不可读: {e})")
    _hdr("最近提交")
    import subprocess as _sp
    r = _sp.run(["git", "-C", AISV_ROOT, "log", "--oneline", "-5"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace")
    print("\n".join("  " + l for l in r.stdout.strip().splitlines()))
    return 0


def main():
    import argparse
    global SHOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default=SHOT,
                    help="episodes.json 场景 key（多场景批量，模板名 api_workflow_twopass_{shot}_{variant}）")
    ap.add_argument("--seed-offset", type=int, default=0, help="RUNS 种子偏移（多场景用不同种子域）")
    ap.add_argument("--confirm-ref-desc", action="store_true",
                    help="确认接力 ref 图与模板首帧描述已对照一致（双步铁律，2026-08-26）")
    ap.add_argument("--status", action="store_true",
                    help="新会话一键恢复报告（当前批/产物/relay 链/待办/H3），不跑批")
    args = ap.parse_args()
    SHOT = args.shot
    if args.status:
        sys.exit(status_report())

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log("op", f"===== twopass auto batch start (shot={SHOT}) =====", runs=RUNS)
    if not h3_alive():
        log("error", "H3 offline — start it first")
        sys.exit(1)
    if not check_sage_flag():
        log("error", "H3 sage flag 不符 — abort（重启 H3 带正确 flag）")
        sys.exit(1)
    if not check_video_ref_frames():
        log("error", "preflight: video_ref 帧数 < 5 — abort（重编码截取修复）")
        sys.exit(1)
    if not preflight():
        log("error", "preflight failed — abort")
        sys.exit(1)
    if not check_ref_desc_sync(args.confirm_ref_desc):
        log("error", "preflight: ref 描述同步未确认 — abort（双步铁律）")
        sys.exit(1)
    if not scene_chain_check():
        log("error", "preflight: 场景链检查失败 — abort（scene_inherit 设计未完成）")
        sys.exit(1)
    if not c_layer_check():
        log("error", "preflight: C 层审查失败 — abort")
        sys.exit(1)
    if not prompt_validate():
        log("error", "preflight: prompt 校验失败 — abort（validate_h3_prompt）")
        sys.exit(1)

    # 续跑安全：先等残留队列排空，再按产物存在性跳过已完成 run
    if not wait_queue_drain():
        log("error", "queue drain failed — abort")
        sys.exit(1)

    results = []
    for idx, (variant, seed) in enumerate(RUNS):
        seed += args.seed_offset
        # 周期重启（2026-08-27 用户定取消：RAM 稳定；RESTART_EVERY=0 时跳过）
        if RESTART_EVERY and idx > 0 and idx % RESTART_EVERY == 0:
            restart_h3()
            if not h3_alive():
                log("error", "H3 down after restart — abort")
                break

        # RAM 守护
        ram = free_ram_gb()
        log("op", f"free RAM before run: {ram:.1f} GB")
        if ram < RAM_MIN_GB:
            free_memory()
            time.sleep(15)
            ram = free_ram_gb()
            log("op", f"free RAM after /free: {ram:.1f} GB")
            if ram < RAM_MIN_GB:
                restart_h3()
                if not h3_alive():
                    log("error", "H3 down after RAM restart — abort")
                    break

        res = run_one(variant, seed)

        # OOM → 换种子重试一次
        if res["result"] in ("oom", "error") and "retried" not in res:
            free_memory()
            time.sleep(20)
            log("warn", f"retry [{variant} s{seed}] with seed {seed + 1000}")
            res2 = run_one(variant, seed + 1000)
            res2["retried"] = True
            res2["orig_seed"] = seed
            results.append(res2)
        else:
            results.append(res)

        free_memory()  # 每发结束放显存/RAM

    ok = [r for r in results if r["result"] == "ok"]
    log("op", f"===== batch end: {len(ok)}/{len(results)} ok =====")
    for r in results:
        log("sum", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
