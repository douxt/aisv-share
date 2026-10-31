#!/usr/bin/env python3
"""build_twopass.py — 官方 LBH r2v 模板 UI→API 转换 + shot_01 内容移植（2026-08-21，v2 2026-08-22）

产出两个 API 工作流（diag/），全部用社区最佳实践参数（2026-08-22 调研定论）：
  api_workflow_twopass_no_lora.json — 无 LoRA：Pass1 16 次迭代（32 步 simple 计划前半）
  api_workflow_twopass_v4.json      — ref2v turbo v4 LoRA：Pass1 6 次迭代（12 步计划前半）

共享参数（社区验证）：
  Pass1: 0.26MP 672x384 + euler + SigmaShift(12,3) → denoised_output（clean latent）
  → LTXVSeparateAVLatent → MinimaxH3LatentUpscaler3D(scale by multiplier 2.0 → 1344x768)
  + audio 绕行 → LTXVConcatAVLatent → Pass2(euler, ManualSigmas 4 步)
  → VAEDecode + VAEDecodeAudio → CreateVideo → SaveVideo

v1→v2 变更（昨夜跑批实测 + 社区调研）：
  1. Pass1 0.2MP → 0.26MP（672x384，与抽卡同分辨率）
  2. 放大 megapixels 1.0(2.21x) → scale 2.0 精确 2x
  3. Pass2 3 步 → 4 步（官方 #22 残留值 0.9035/0.8000/0.6316/0.3158/0）
  4. official(fl2v lora) → no_lora（无 LoRA，16 次迭代）
  5. lorav4 4 步 → 6 次迭代（v4 最佳步数，消除 4 步拖影）
"""
import json
import os
import sys

from common import AISV_ROOT, H3_ROOT, SCENE, SCENE_DIR, SCRIPT_DIR, SPEC_PATH

OUT_DIR = SCRIPT_DIR
EPISODES = os.path.join(SCENE_DIR, "configs", "episodes.json")

# 稳定律（2026-08-30）：常量从 twopass_spec.json 读（唯一事实源），防散落硬编码漂移。
# argparse 默认值同样从 spec 读（main()）— 此常量仅作兜底。定义必须先于全部引用（2026-09-01 修）。
_SPEC_DEFAULTS = json.load(open(SPEC_PATH, encoding="utf-8"))
UNET_PRUNED = _SPEC_DEFAULTS["unet_name"]  # spec 定案
LORA_V4 = "minimax_h3_turbo_v4_step600_ema.safetensors"
LORA_FL2V = "Minimax_H3\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
LORA_FL2V11 = "Minimax_H3\\minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors"  # S8① 官方新版（JZL 标配）
LORA_REF2V = "Minimax_H3\\minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"  # S8①b 官方 ref2va 加速
UPSCALER_MODEL = "minimax_h3_latent_upscaler_3d_fp16.safetensors"
PASS2_SIGMAS = _SPEC_DEFAULTS.get("pass2_sigmas", "0.9035, 0.8000, 0.6316, 0.3158, 0.0000")
PASS1_MP = _SPEC_DEFAULTS["pass1_mp"]                  # spec 定案（384p = 0.26）
UPSCALE_SCALE = _SPEC_DEFAULTS["upscale_scale"]        # spec 定案（384p→704 = 1.8333）

# 变体定义: (名, LoRA 名或 None, Pass1 迭代次数, BasicScheduler 步数[2x 计划切前半], mode)
# mode: "twopass" = 放大+二采全链；"draw" = Pass1 后直接解码（抽卡模板，与 twopass Pass1 同构）
VARIANTS = [
    ("no_lora", None, 16, 32, "twopass"),
    ("v4", LORA_V4, 6, 12, "twopass"),
    ("fl2v", LORA_FL2V, 4, 8, "twopass"),  # 新参数补跑：fl2v 推荐 4 步 + 同基线
    ("v4_draw", LORA_V4, 6, 12, "draw"),
    ("fl2v_draw", LORA_FL2V, 4, 8, "draw"),  # L1 快筛层（prompt 方向验证，~5min/发，不锁定）
    ("no_lora_draw", None, 16, 32, "draw"),  # 分路线抽卡（2026-08-23）：无 LoRA 抽卡基线
    ("fl2v11", LORA_FL2V11, 4, 8, "twopass"),  # S8①：官方 v1.1 新版（2026-08-23 下载）
    ("fl2v11_draw", LORA_FL2V11, 4, 8, "draw"),  # S8① 抽卡对照
    ("fl2v11_draw_6step", LORA_FL2V11, 6, 12, "draw"),  # 手部崩坏实验：同 LoRA 超步数 6 步（2026-08-23）
    ("fl2v11_draw_8step", LORA_FL2V11, 8, 16, "draw"),  # 手部崩坏实验：同 LoRA 超步数 8 步
    ("ref2v", LORA_REF2V, 4, 8, "twopass"),  # S8①b：官方 ref2va 加速 LoRA（2026-08-23 下载）
    ("ref2v_draw", LORA_REF2V, 4, 8, "draw"),
]


def load_shot(name: str = "shot_01"):
    """episodes.json 场景读取（shot_01 / act2_2a / shot_battle ...）。
    兼容两种结构（顶层直接是 shot 字典 / 包在 "shots" 下）。
    episodes.json 用 image_0..image_N / audio_N 平面键，图数 3-6 不等 → 动态上限。"""
    cfg = json.load(open(EPISODES, encoding="utf-8"))
    shots = cfg.get("shots", cfg)
    s = None
    for k, v in shots.items():
        if k == name or v.get("shot") == name or v.get("name") == name:
            s = v
            break
    if s is None:
        raise RuntimeError(f"{name} not found in episodes.json")
    s = dict(s)
    n_img = max([int(k[6:]) for k in s if k.startswith("image_")], default=-1) + 1
    n_aud = max([int(k[6:]) for k in s if k.startswith("audio_")], default=-1) + 1
    s["images"] = [s[f"image_{i}"] for i in range(n_img) if s.get(f"image_{i}")]
    s["audio"] = [s[f"audio_{i}"] for i in range(n_aud) if s.get(f"audio_{i}")]
    return s


def build(variant: str, lora_name: str | None, pass1_steps: int, sched_steps: int,
          mode: str = "twopass", duration: float | None = None,
          pass1_mp: float = PASS1_MP, upscale_scale: float = UPSCALE_SCALE,
          shot_name: str = "shot_01", unet: str = "ref2va",
          audio_shift: float = 6.0, chain: str = "t8mars",
          video_ref: str | None = None) -> dict:  # 12/6 定案（2026-08-25 A/B：响度+7.5% 细节略丰，社区 Abiray 主流）
    """官方模板 → API dict。节点 ID 沿用官方（1-38），新增 900-905。

    mode="draw": 跳过后链（放大/二采），Pass1 denoised_output 直接解码 —— 抽卡模板。
    Pass1 子图与 twopass 版完全同构（同 seed = 同 latent，供画面锁定验证）。
    duration: 覆盖时长（验证用 5s 省 ~2.6× 时间，交付仍 15s）。
    pass1_mp/upscale_scale: 小倍率路线参数（S8④ 候选，如 0.41MP + 1.5 → 864×480 → 1280×704）。
    shot_name: episodes.json 场景 key（多场景批量交付，2026-08-23）。"""
    shot = load_shot(shot_name)
    if duration is not None:
        shot["duration"] = duration
    images = shot["images"]  # 0 号走官方 #9 槽
    audio = shot["audio"]  # 0-N 个音色 ref（shot_battle 双音色，v23 用 Audio 1+Audio 2）

    wf = {}

    def node(nid, class_type, inputs, meta=None):
        n = {"inputs": inputs, "class_type": class_type}
        if meta:
            n["_meta"] = meta
        wf[nid] = n

    # ── 模型加载 ──
    node("3", "VAELoader", {"vae_name": "minimax_h3_video_vae_fp16.safetensors"})
    node("4", "VAELoader", {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"})
    node("15", "CLIPLoader", {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                              "type": "minimax", "device": "default"})
    if unet == "fl2va":
        node("30", "UnetLoaderGGUF", {"unet_name": "minimax_h3_fl2va_pruned_fp8_U16G.gguf"})
    else:
        node("30", "UNETLoader", {"unet_name": UNET_PRUNED, "weight_dtype": "default"})
    if lora_name:
        node("36", "LoraLoaderModelOnly", {"model": ["30", 0], "lora_name": lora_name,
                                           "strength_model": 1.0},
             {"title": f"Turbo LoRA ({variant})"})
        sigma_model = "36"
    else:
        sigma_model = "30"  # 无 LoRA：UNET 直连 SigmaShift
    node("38", "MiniMaxH3SigmaShift", {"model": [sigma_model, 0], "shift_video": _SPEC_DEFAULTS["shift_video"],
                                       "shift_audio": audio_shift})

    # ── 内容输入 ──
    node("2", "PrimitiveStringMultiline", {"value": shot["prompt"]}, {"title": "Prompt"})
    node("9", "LoadImage", {"image": images[0]}, {"title": "Reference (ref_image_0)"})
    for i in range(1, len(images)):
        node(str(900 + i), "LoadImage", {"image": images[i]},
             {"title": f"Reference (ref_image_{i})"})
    for i, a in enumerate(audio):
        node(str(950 + i), "LoadAudio", {"audio": a},
             {"title": f"Voice Reference (ref_audio_{i})"})
    # ── Ref2VA 入口 ──
    ref2va_inputs = {
        "ref_image_size": "match",
        "clip": ["15", 0],
        "vae": ["3", 0],
        "audio_vae": ["4", 0],
        "prompt": ["2", 0],
        "width": ["20", 0],
        "height": ["20", 1],
        "length": ["19", 1],
        "ref_images.ref_image_0": ["9", 0],
    }
    for i in range(1, len(images)):
        ref2va_inputs[f"ref_images.ref_image_{i}"] = [str(900 + i), 0]
    for i in range(len(audio)):
        ref2va_inputs[f"ref_audios.ref_audio_{i}"] = [str(950 + i), 0]
    if video_ref:
        # 接缝视频 context 接力（2026-08-30 试点，MacroSony 22 帧方案简化版）：
        # 上镜尾段作 ref_videos 条件 → 首帧自然延续，图 ref 全保留
        # 注意：ref_videos 输入类型 = IMAGE（帧序列）→ 用 VHS_LoadVideo（输出 IMAGE），非 LoadVideo（VIDEO）
        node("960", "VHS_LoadVideo", {"video": video_ref, "force_rate": 0, "force_size": "Disabled",
                                      "custom_width": 864, "custom_height": 480, "frame_load_cap": 0,
                                      "skip_first_frames": 0, "select_every_nth": 1},
             {"title": "Tail context video"})
        ref2va_inputs["ref_videos.ref_video_0"] = ["960", 0]
    node("11", "MiniMaxH3ReferenceToVideo", ref2va_inputs)

    # ── 时长/分辨率/调度 ──
    node("17", "PrimitiveFloat", {"value": float(shot["duration"])}, {"title": "Duration (s)"})
    node("19", "ComfyMathExpression", {
        "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
        "values.a": ["17", 0]})
    node("20", "ResolutionSelector", {"aspect_ratio": "16:9 (Widescreen)",
                                      "megapixels": pass1_mp, "multiple": 32})
    node("31", "RandomNoise", {"noise_seed": 42})
    full_sigma = mode == "draw"
    if mode == "draw":
        # draw 全量 sigma（2026-08-24 修正，调研定论）：半程 SplitSigmas 解码 = latent 停 sigma ~0.92
        # = 92% 残噪当成品（画面糊 + 音频时钟错位爆噪）。全量 = BasicScheduler 直连，步数=pass1_steps。
        node("18", "KSamplerSelect", {"sampler_name": _SPEC_DEFAULTS["sampler"]})
        node("24", "BasicScheduler", {"scheduler": _SPEC_DEFAULTS["scheduler"],
                                      "steps": pass1_steps,
                                      "denoise": 1, "model": ["30", 0]})
        sampler_in = ["18", 0]
        sigmas_pass1 = ["24", 0]
    elif chain == "legacy":
        # 旧链保守精修（2026-08-25 双链定案）：SplitSigmas 手切 + ManualSigmas [0.9035→0]
        # 部分去噪精修 — >10s 长镜末帧稳定（T8mars 激进链 15s 末端退化 ✗）。社区：long-video stability better。
        node("18", "KSamplerSelect", {"sampler_name": _SPEC_DEFAULTS["sampler"]})
        node("24", "BasicScheduler", {"scheduler": _SPEC_DEFAULTS["scheduler"], "steps": 8,
                                      "denoise": 1, "model": ["30", 0]})
        node("13", "SplitSigmas", {"sigmas": ["24", 0], "step": 4})
        node("33", "ManualSigmas", {"sigmas": PASS2_SIGMAS})
        sampler_in = ["18", 0]
        sigmas_pass1 = ["13", 0]      # 8 步计划切前半 4 步
    else:
        # T8mars 8-21 官方演进（2026-08-25 调研）：Parity Plan（base8/coarse4/refine4）+ DualClockSampler
        node("40", "MiniMaxH3LearnedTwoPassParityPlanT8Advanced", {
            "model": ["30", 0], "base_steps": 8, "coarse_steps": 4, "refine_steps": 4,
            "shift_video": _SPEC_DEFAULTS["shift_video"], "shift_audio": audio_shift})
        node("41", "MiniMaxH3DualClockSamplerT8", {
            "model": ["30", 0], "av_latent": ["11", 1], "steps": 4,
            "shift_video": _SPEC_DEFAULTS["shift_video"], "shift_audio": audio_shift})
        sampler_in = ["41", 1]        # dual_clock_euler（官方）
        sigmas_pass1 = ["40", 0]      # coarse_sigmas
        sigmas_pass2 = ["40", 1]      # refine_sigmas（替代 ManualSigmas PASS2_SIGMAS）

    # ── 两遍采样 ──
    node("32", "BasicGuider", {"model": ["38", 0], "conditioning": ["11", 0]},
         {"title": "Guider (Pass1)"})
    node("21", "SamplerCustomAdvanced", {"noise": ["31", 0], "guider": ["32", 0],
                                         "sampler": sampler_in,
                                         "sigmas": sigmas_pass1,
                                         "latent_image": ["11", 1]},
         {"title": "Sampler Pass1 (低分辨率主采样)"})
    if mode == "draw":
        decode_latent = ["21", 1]  # denoised_output（clean，全量 sigma 走完）直接解码 = 抽卡
    else:
        # ── 潜空间放大（video 放大 + audio 绕行） ──
        node("35", "LTXVSeparateAVLatent", {"av_latent": ["21", 1]})
        node("26", "MinimaxH3LatentUpscaler3D", {
            "latent": ["35", 0],
            "model_name": UPSCALER_MODEL,
            "mode": "scale by multiplier",
            "mode.scale": upscale_scale,
            "align": 32,
            "device": "cuda",
            "precision": "fp16"})
        node("10", "LTXVConcatAVLatent", {"video_latent": ["26", 0], "audio_latent": ["35", 1]})
        if chain == "legacy":
            # 旧链 Pass2：放大 latent 直接精修（无双时钟/Reconcile/DetailMixer）
            node("29", "BasicGuider", {"model": ["38", 0], "conditioning": ["11", 0]},
                 {"title": "Guider (Pass2)"})
            node("16", "SamplerCustomAdvanced", {"noise": ["31", 0], "guider": ["29", 0],
                                                 "sampler": sampler_in, "sigmas": ["33", 0],
                                                 "latent_image": ["10", 0]},
                 {"title": "Sampler Pass2 (低 sigma 精修)"})
            decode_latent = ["16", 0]
        else:
            # T8mars 8-21 Pass2 链（2026-08-25 冒烟修复）：AudioConditioning（高清模板 latent）
            # + Reconcile（latent/cond 维度对齐）+ DetailMixer（sampler+sigmas）
            node("44", "MiniMaxH3AudioConditioningT8", {
            "clip": ["15", 0], "video_vae": ["3", 0], "audio_vae": ["4", 0],
            "prompt": ["2", 0], "width": 1280, "height": 704, "length": ["19", 1],
            "task_type": "Ref2VA", "audio_mode": "native", "audio_denoise_strength": 1.0,
            "add_source_as_reference": False, "prompt_primary_audio_ordinal": 0,
            "strict_prompt_tags": True, "ref_image_size": "match",
            "reference_video_policy": "official_2_to_15s",
            **{f"ref_images.ref_image_{i}": ["9" if i == 0 else str(900 + i), 0]
               for i in range(len(images))},
            **{f"ref_audios.ref_audio_{i}": [str(950 + i), 0] for i in range(len(audio))}})
            node("42", "MiniMaxH3TwoPassLatentReconcileT8Advanced", {
                "learned_latent": ["10", 0], "highres_template": ["44", 1],
                "positive": ["44", 0],
                "audio_policy": "auto", "second_pass_audio_source": "legacy_policy",
                "second_pass_audio_strength": 0.0})
            node("43", "MiniMaxH3TwoPassDetailMixerT8Advanced", {
                "model": ["30", 0], "av_latent": ["42", 0], "refine_sigmas": ["40", 1],
                "shift_video": _SPEC_DEFAULTS["shift_video"], "shift_audio": audio_shift,
                "enable_tail": False, "extra_tail_steps": 3, "tail_spacing": "video_sigma_linear",
                "enable_model_time_bias": False, "bias": -0.025, "bias_start_progress": 0.7,
                "bias_end_progress": 0.95, "bias_domain": "video_sigma",
                "enable_stg": False, "stg_scale": 0.35, "stg_double_blocks": "25",
                "stg_start_progress": 0.25, "stg_end_progress": 0.85,
                "enable_restart": False, "restart_video_sigma": 0.15, "restart_steps": 3,
                "restart_seed": 2608193401})
            node("29", "BasicGuider", {"model": ["38", 0], "conditioning": ["42", 1]},
                 {"title": "Guider (Pass2)"})
            node("16", "SamplerCustomAdvanced", {"noise": ["31", 0], "guider": ["29", 0],
                                                 "sampler": ["43", 1], "sigmas": ["43", 2],
                                                 "latent_image": ["42", 0]},
                 {"title": "Sampler Pass2 (低 sigma 精修)"})
            decode_latent = ["16", 0]

    # ── 解码输出 ──
    node("25", "VAEDecode", {"samples": decode_latent, "vae": ["3", 0]})
    node("14", "VAEDecodeAudio", {"samples": decode_latent, "vae": ["4", 0]})
    node("34", "CreateVideo", {"fps": 24, "bit_depth": 8, "images": ["25", 0],
                               "audio": ["14", 0]})
    # 统一输出前缀（2026-08-23 定案）：magic/{act}/{shot}/{shot}_{variant}，按幕分目录
    out_prefix = f"{SCENE}/{shot.get('act', 'misc')}/{shot_name}/{shot_name}_{variant}"
    node("5", "SaveVideo", {"filename_prefix": out_prefix,
                            "format": "auto", "codec": "auto", "video": ["34", 0]})

    return wf


def main():
    import argparse
    # 稳定律（2026-08-30）：默认值全部从 twopass_spec.json 读（唯一事实源）。
    # 任何"默认跑" = 规格锁定参数；显式传参才 override。改规格 = git 可见 + 用户审。
    spec = json.load(open(SPEC_PATH, encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=None,
                    help="override duration (s). 验证用 5，交付用默认 15。文件名带 _5s 后缀")
    ap.add_argument("--pass1-mp", type=float, default=spec["pass1_mp"],
                    help=f"override Pass1 megapixels（默认=规格 {spec['pass1_mp']} = {spec['pass1_resolution']}，480p 定案）")
    ap.add_argument("--scale", type=float, default=spec["upscale_scale"],
                    help=f"override upscale scale（默认=规格 {spec['upscale_scale']} → {spec['deliver_resolution']}）")
    ap.add_argument("--label", default=spec["label"],
                    help=f"filename label（默认=规格 '{spec['label']}'；改 label = 命名即契约要配套校验）")
    ap.add_argument("--shot", default="shot_01",
                    help="episodes.json 场景 key（shot_01/act2_2a/act2_2b/shot_battle...）")
    ap.add_argument("--unet", default="ref2va", choices=["ref2va", "fl2va"],
                    help="UNET 选择（A/B：fl2v11 LoRA 配 fl2va 验证）")
    ap.add_argument("--audio-shift", type=float, default=spec["audio_shift"],
                    help=f"audio sigma shift（默认=规格 {spec['audio_shift']}；A/B 验证过 12/3）")
    ap.add_argument("--chain", default=spec["chain"], choices=["t8mars", "legacy"],
                    help="二采链型（2026-08-30 定案：默认 legacy。T8mars 节点已随 E3 删包（2026-08-26），t8mars 需环境有 T8 节点否则提交失败；默认值曾=t8mars 导致配置突变事故）")
    ap.add_argument("--video-ref", default=None,
                    help="上镜尾段视频 ref（接缝 context 接力试点：ref_videos.ref_video_0，路径 = H3 input 文件名）")
    args = ap.parse_args()

    shot = load_shot(args.shot)
    print(f"[{args.shot}] duration={shot['duration']} images={len(shot['images'])} "
          f"audio={shot.get('audio')}")

    for variant, lora, pass1_steps, sched_steps, mode in VARIANTS:
        wf = build(variant, lora, pass1_steps, sched_steps, mode,
                   duration=args.duration,
                   pass1_mp=args.pass1_mp if args.pass1_mp else PASS1_MP,
                   upscale_scale=args.scale if args.scale else UPSCALE_SCALE,
                   shot_name=args.shot, unet=args.unet, audio_shift=args.audio_shift,
                   chain=args.chain, video_ref=args.video_ref)
        suffix = f"_{int(args.duration)}s" if args.duration else ""
        label = f"_{args.label}" if args.label else ""
        # 默认 shot_01 保持原文件名（runner 引用不变）；多场景加场景前缀防撞名
        # legacy 链恒含 shot：legacy_{shot}_{variant}（与 c5a7fd8 入库模板命名一致）
        if args.chain == "legacy":
            stem = f"{args.shot}_{variant}"
        else:
            stem = variant if args.shot == "shot_01" else f"{args.shot}_{variant}"
        if args.unet == "fl2va":
            stem += "_fl2va"
        if args.audio_shift != 6.0:
            stem += f"_s{int(args.audio_shift)}"
        chain_prefix = "legacy_" if args.chain == "legacy" else ""
        out = os.path.join(OUT_DIR, f"api_workflow_twopass_{chain_prefix}{stem}{label}{suffix}.json")
        json.dump(wf, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[written] {out} ({len(wf)} nodes, Pass1 {pass1_steps} iters, "
              f"mode={mode}, chain={args.chain})")

    # 预检输出分辨率：Pass1 0.26MP 16:9 → 672×384；scale 2.0 → 1344×768
    # 稳定律：按实际参数打印（2026-08-30 修：节点按 1024^2/MP 定义，round 对齐 32 — 与 ResolutionSelector 一致）
    mp = args.pass1_mp if args.pass1_mp else PASS1_MP
    sc = args.scale if args.scale else UPSCALE_SCALE
    import math
    scale_f = math.sqrt(mp * 1024 * 1024 / (16 * 9))
    w = round(16 * scale_f / 32) * 32
    h = round(9 * scale_f / 32) * 32
    print(f"[precheck] Pass1: {mp}MP 16:9 multiple=32 -> {w}x{h}")
    print(f"[precheck] Upscale: scale {sc} -> {round(w*sc/32)*32}x{round(h*sc/32)*32}")


if __name__ == "__main__":
    main()
