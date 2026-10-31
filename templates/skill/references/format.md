# 格式源（模板）

> 这是**权威格式**。skill 每次生成前都要读它，不要凭记忆。
> 把下面的段落换成你自己的模型/供应商要求的格式。

## 段落顺序（必须一致）

```
subject_definitions
summary
retention_analysis
detailed_description
overall_soundscape
non_diegetic_music
```

改动方式：修改 `scripts/validate.py` 顶部的 `REQUIRED_SECTIONS`，顺序即校验顺序。

## 每段要求

| 段 | 内容 | 最少 |
|---|---|---|
| subject_definitions | 每个主体一句定义，绑定参考图编号 | 主体数与参考图数一致 |
| summary | 一句任务类型 + 一句剧情 | 1–3 句 |
| retention_analysis | 每个主体/参考一行，声明保留程度 | 与主体一一对应 |
| detailed_description | 正文：风格句 → 时间轴事件 → 运镜 → 台词 → 落定 | 200 词以上 |
| overall_soundscape | 物理声与环境声，不重复台词 | 1–3 句 |
| non_diegetic_music | 角色听不到的配乐，写乐器与动态 | 1–2 句 |

## 时间戳

- 必须出现在 `detailed_description`；
- 单调递增；
- 不超过镜头时长；
- 最后一点应接近结尾（建议 ≥ 时长的 60%）。

## 参考图

- 用 `<Picture N>` 表示，N 从 1 开始；
- 所有被引用的编号必须在 `subject_definitions` 中定义；
- 衔接镜：首帧图必须是 `<Picture 1>`，且定义里写明「上一镜的最后一帧」；
- 总张数不超过模型上限（默认 5，见 `--max-refs`）。

## 负面指令

禁止写「不要 X / 不出现 Y」。改写为正面目标状态。

例外：整段的质感约束句（如 `No stiff motion, no frozen limbs`）允许，需加入 `validate.py` 的 `NEGATIVE_ALLOW`。
