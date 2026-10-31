# 代码包（H3 版）

> 18 个脚本 + 配置。路径通过环境变量或 `twopass_spec.json` 的 `env` 段定位；模板由代码生成。

这是 2026-09 生产线的快照。它跑出了《感谢祭之冰》EP01；但它不是通用框架，也不跟随未来版本更新。

---

## 1. 目录

| 文件 | 作用 |
|---|---|
| `common.py` | 路径解析（环境变量 > spec.env > 默认） |
| `twopass_spec.json` | 配置唯一事实源 |
| `build_episodes.py` | 镜头 md → `episodes.json` |
| `build_twopass.py` | `episodes.json` → 生成模板（节点图在代码里构建） |
| `run_twopass_auto.py` | 跑批（抽卡/二采）+ preflight + `--status` |
| `relay.py` | 尾帧 + 尾段截取（重编码、精确帧数） |
| `sync_assets.py` | 项目资产 → H3 input 单向同步 |
| `quality_gate.py` | 首帧像素差 / 分辨率门禁 |
| `check_frames.py` / `tail_check.py` | 门禁依赖（像素差 / 尾段检测） |
| `seam_join.py` / `check_seam.py` | 两镜接缝（交叉淡化 + 验收） |
| `join_clips.py` | 全片拼接（逐镜实测累加） |
| `upscale_batch.py` / `upscale_frames.py` | 逐镜分批超分（需 torch/spandrel） |
| `audio_check.py` | 峰值 / 台词窗 RMS |
| `chain_check.py` | md ↔ episodes ↔ 模板 三层一致性 |
| `check_scene_chain.py` | 场景继承链（纯文本） |
| `check_lockframe_desc.py` | 构图尺度一致性（纯文本） |

---

## 2. 前置

| 项 | 要求 |
|---|---|
| Python | 3.10+ |
| ffmpeg / ffprobe | 在 PATH |
| H3 实例 | 8188，带 `--disable-pinned-memory --cache-ram 16` |
| 平台 | **Windows**（脚本使用 PowerShell / netstat / 盘符路径） |
| Python 依赖 | 标准库 + `Pillow`、`opencv-python`（门禁/接缝）、`numpy`/`torch`/`spandrel`（仅超分） |
| git | `build_episodes.py` 的漂移检测会调用 |

项目目录假设：

```
<AISV_ROOT>/
└── <SCENE>/                 # 默认 magic
    ├── docs/prompts/        # 镜头 md（含 frontmatter 与 ```text 块）
    ├── assets/              # 角色 / 场景 / 音色 / shots
    ├── configs/episodes.json
    ├── output/
    └── preview/
```

---

## 3. 配置

编辑 `twopass_spec.json` 的 `env`：

```json
"env": {
  "h3_root": "C:/AI/ComfyUI_H3",
  "h3_port": 8188,
  "project_root": ".",
  "scene_dir": "magic"
}
```

或临时用环境变量覆盖：

| 变量 | 含义 |
|---|---|
| `AISV_PROJECT_ROOT` | 项目根 |
| `AISV_H3_ROOT` | H3 实例根 |
| `AISV_SCENE` | 子项目目录名（默认 magic） |
| `AISV_SPEC` | spec 路径 |
| `AISV_UPSCALE_MODEL` | 超分模型路径 |

---

## 4. 快速自检（不耗 GPU）

在**项目根**（包含 `magic/` 的目录）执行：

```bash
python code/run_twopass_auto.py --status     # 配置 / 当前镜 / 产物 / H3 状态
python code/sync_assets.py --check           # 资产双份一致性
python code/chain_check.py check --shot <镜> --md <md> --episodes <episodes> --template <路径>
python code/quality_gate.py <产物.mp4> --ref <尾帧.png>
```

**模板路径建议传绝对路径**（相对路径按当前工作目录解析）。

---

## 5. 不包含

| 未包含 | 原因 |
|---|---|
| `check_ref_desc.py` / `vlm_desc.py` / `prompt_review.py` | 依赖本地视觉模型环境，preflight 缺失时显式跳过 |
| `validate_h3_prompt.py` | 项目专属的提示词规则校验器 |
| `auto_pipeline.py` / `auto_chain.py` / `card_selector.py` / watchdog | 全自动编排与具体项目耦合深 |
| 模型权重、LoRA、资产图、音色 | 许可与授权 |
| 项目专属规则（战斗技能表等） | 属于作品设定 |

---

## 6. 快照声明

- 代码是**某一时刻的可用版本**，不保证与文档或未来版本同步；
- 修复顺序：先改你自己这份，再把值得回流的部分提给上游；
- 已知耦合：模板由 `build_twopass.py` 按节点名构建；H3 节点增删后模板与脚本都要跟着改。

## 7. 快照维护（维护者）

`code/` 是手工维护的 **fork**：路径已按本包布局改造，不随上游自动更新。
`SOURCE.json` 记录上游文件在同步时的哈希，用于发现「上游变了」。

```bash
python tools/sync_code.py --check    # 上游是否变动；有漂移 exit 1
python tools/sync_code.py --record   # 人工合并后，记录当前上游哈希
```

工作流：`--check` 报漂移 → 人工把有价值的改动合并进 `code/` → `--record` 更新清单。
清单只含相对路径与哈希，不含私有仓名或提交号。
