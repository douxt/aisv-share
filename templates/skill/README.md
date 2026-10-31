# 提示词技能模板（可运行）

这是提示词技能骨架（六段式）。**复制走，改成你的。**
我们自己的实例说明见 `03-stack/提示词技能.md`；检查链的设计见 `01-method/05-提示词检查链.md`。

## 三步用起来

```bash
# 1. 复制到你的项目
cp -r skill <your-project>/.claude/skills/<your-skill>

# 2. 改两处
#    scripts/validate.py 顶部：REQUIRED_SECTIONS + 各正则 + 否定白名单
#    references/format.md：换成你的模型要求的段名与格式

# 3. 自检（必须 6/6）
cd <your-skill>
python scripts/validate.py --selftest
```

## 目录

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 规程：识别 → 读格式源 → 生成 → 校验 → 报告 → 停机 |
| `references/format.md` | 权威格式源（段序、时间戳、参考图规则） |
| `references/examples.md` | 正例/反例成对（最有用的部分） |
| `scripts/validate.py` | 机械检查器，零依赖，带自测 |
| `samples/` | 6 份回归样本：1 好 + 5 各错一种 |

## 检查器覆盖什么

| 规则 | 级别 | 查什么 |
|---|---|---|
| `L1.sections` | ERROR | 段落缺失、顺序错误 |
| `L1.timestamp` | ERROR/WARN | 非单调、超时长、末点过早 |
| `L1.labels` | ERROR | 引用了未定义的 `<Picture N>` |
| `L2.negative` | ERROR | 否定指令（白名单除外） |
| `L2.steps` | ERROR | 「三步」类计数写法 |
| `L2.meters` | WARN | 精确米数 |
| `L2.empty_adj` | WARN | cinematic/epic 类空词 |
| `L3.budget` | ERROR | 参考图超上限 |
| `L3.lockframe` | WARN | 锁帧镜未写「上一镜最后一帧」/ 首图不在首位 |

## 加你自己的检查

1. 在 `references/examples.md` 加一对正/反例；
2. 在 `validate.py` 加一条正则 + 一个 `Issue(...)`；
3. **在 `samples/` 加一份只犯这个错的文件**，并在 `selftest()` 的 cases 里登记；
4. 跑 `--selftest`，确认：好样本仍通过、新坏样本被正确拦下。

第 3 步不能省。没有回归样本的规则，改一次格式就可能静默失效。

## 边界

- 只覆盖**机械层**。语义层（描述与参考图是否一致、空间是否成立）需要视觉模型，见 `01-method/05-提示词检查链.md`；
- 自测 6/6 只证明「这几条规则有牙」，不证明「覆盖到位」；
- 阈值（时长比例、参考图上限）要按你的栈重标定。
