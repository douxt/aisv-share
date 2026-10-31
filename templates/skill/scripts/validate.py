#!/usr/bin/env python3
"""prompt validator skeleton — stdlib only, no external deps.

用法:
  python validate.py <prompt.md> --duration 10 [--max-refs 5] [--lockframe]
  python validate.py --selftest

退出码: 0 = 无 ERROR / 1 = 有 ERROR / 2 = 用法或工具错误

改你自己的格式: 修改 REQUIRED_SECTIONS 与各正则即可。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- 配置区
REQUIRED_SECTIONS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]

# 允许的整段质感否定句（先剔除再扫描）
NEGATIVE_ALLOW = [
    r"no\s+stiff\s+motion",
    r"no\s+frozen\s+limbs",
]

NEGATIVE_RE = re.compile(
    r"\b(?:no|not|never|without|avoid|does\s+not|doesn't|don't)\b", re.I
)
EMPTY_ADJ = ["cinematic", "epic", "beautiful", "stunning", "breathtaking", "masterpiece"]
EMPTY_RE = re.compile(r"\b(?:" + "|".join(EMPTY_ADJ) + r")\b", re.I)
# 排除 "one step"（这是推荐写法：写终点而不是步数）
STEP_RE = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+(?:slow\s+)?steps?\b",
    re.I,
)
METER_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:m|meters?|metres?)\b", re.I)
TS_RE = re.compile(r"(?<![\w:])(?:(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?|(\d+(?:\.\d+)?)\s*s)\b")
PIC_RE = re.compile(r"<(?:Picture|Image)\s+(\d+)>", re.I)
SECTION_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*:\s*$")
# ---------------------------------------------------------------- 配置区结束


class Issue:
    def __init__(self, level: str, rule: str, line: int, msg: str):
        self.level, self.rule, self.line, self.msg = level, rule, line, msg

    def render(self) -> str:
        return f"[{self.level}][{self.rule}] line {self.line}: {self.msg}"


def parse_sections(text: str):
    """返回 {section: (start_line, text)} 与出现顺序。"""
    sections: dict[str, tuple[int, int]] = {}
    order: list[str] = []
    lines = text.splitlines()
    current = None
    start = 0
    buf: list[str] = []
    for i, line in enumerate(lines, start=1):
        m = SECTION_RE.match(line)
        if m and m.group(1) in REQUIRED_SECTIONS:
            if current:
                sections[current] = (start, i - 1)
            current = m.group(1)
            order.append(current)
            start = i + 1
            buf = []
    if current:
        sections[current] = (start, len(lines))
    body = {}
    for name, (a, b) in sections.items():
        body[name] = "\n".join(lines[a - 1 : b])
    return body, order


def to_seconds(m: re.Match) -> float:
    if m.group(1) is not None:
        mm = int(m.group(1))
        ss = int(m.group(2))
        frac = m.group(3) or "0"
        frac = (frac + "000")[:3]
        return mm * 60 + ss + int(frac) / 1000.0
    return float(m.group(4))


def validate_text(text: str, duration: float | None, max_refs: int, lockframe: bool) -> list[Issue]:
    issues: list[Issue] = []
    body, order = parse_sections(text)

    # L1 结构
    missing = [s for s in REQUIRED_SECTIONS if s not in body]
    if missing:
        issues.append(Issue("ERROR", "L1.sections", 0, "缺少段落: " + ", ".join(missing)))
    if not missing and order != REQUIRED_SECTIONS:
        issues.append(
            Issue("ERROR", "L1.sections", 0, "段落顺序不对: " + " → ".join(order))
        )

    dd = body.get("detailed_description", "")
    sd = body.get("subject_definitions", "")

    # L1 时间戳
    stamps = [(to_seconds(m), m.group(0)) for m in TS_RE.finditer(dd)]
    if not stamps:
        issues.append(Issue("WARN", "L1.timestamp", 0, "detailed_description 中没有时间戳"))
    else:
        vals = [v for v, _ in stamps]
        for prev, cur in zip(vals, vals[1:]):
            if cur < prev:
                issues.append(
                    Issue("ERROR", "L1.timestamp", 0, f"时间戳非单调: {prev} → {cur}")
                )
                break
        if duration is not None:
            if max(vals) > duration + 0.5:
                issues.append(
                    Issue(
                        "ERROR",
                        "L1.timestamp",
                        0,
                        f"最后时间戳 {max(vals):.3f}s 超过镜头时长 {duration:.3f}s",
                    )
                )
            elif max(vals) < duration * 0.6:
                issues.append(
                    Issue(
                        "WARN",
                        "L1.timestamp",
                        0,
                        f"最后时间戳 {max(vals):.3f}s 偏早（< 时长的 60%）",
                    )
                )

    # L1 标签闭合 + L3 预算
    referenced = sorted({int(n) for n in PIC_RE.findall(text)})
    defined = sorted({int(n) for n in PIC_RE.findall(sd)})
    undefined = [n for n in referenced if n not in defined]
    if undefined:
        issues.append(
            Issue("ERROR", "L1.labels", 0, "引用了未定义的参考图: " + ", ".join(map(str, undefined)))
        )
    if len(referenced) > max_refs:
        issues.append(
            Issue("ERROR", "L3.budget", 0, f"参考图 {len(referenced)} 张，超过上限 {max_refs}")
        )

    # L3 锁帧（软检查）
    if lockframe:
        low = sd.lower()
        if not any(k in low for k in ("previous shot", "last frame", "final frame")):
            issues.append(
                Issue("WARN", "L3.lockframe", 0, "锁帧镜但主体定义未写明「上一镜最后一帧」")
            )
        first_positions = {}
        for m in PIC_RE.finditer(sd):
            first_positions.setdefault(int(m.group(1)), m.start())
        if first_positions and min(first_positions) != 1:
            issues.append(Issue("WARN", "L3.lockframe", 0, "锁帧图不是第一个被定义/引用的"))

    # L2 反模式（全篇，先剔除允许句）
    scan = text
    for pat in NEGATIVE_ALLOW:
        scan = re.sub(pat, "", scan, flags=re.I)
    neg = NEGATIVE_RE.search(scan)
    if neg:
        line = scan[: neg.start()].count("\n") + 1
        issues.append(
            Issue("ERROR", "L2.negative", line, f'否定指令 "{neg.group(0)}" — 改写为正面目标状态')
        )
    step = STEP_RE.search(scan)
    if step:
        line = scan[: step.start()].count("\n") + 1
        issues.append(
            Issue("ERROR", "L2.steps", line, f'步数写法 "{step.group(0)}" — 改写终点状态')
        )
    meter = METER_RE.search(scan)
    if meter:
        line = scan[: meter.start()].count("\n") + 1
        issues.append(
            Issue("WARN", "L2.meters", line, f'精确米数 "{meter.group(0)}" — 换参照物锚定')
        )
    empty = EMPTY_RE.search(scan)
    if empty:
        line = scan[: empty.start()].count("\n") + 1
        issues.append(
            Issue("WARN", "L2.empty_adj", line, f'空泛形容词 "{empty.group(0)}" — 换具体杠杆')
        )

    issues.sort(key=lambda i: (0 if i.level == "ERROR" else 1, i.line))
    return issues


def run_file(path: Path, duration, max_refs, lockframe) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERROR][L0.file] 无法读取 {path}: {e}")
        return 2
    if not text.strip():
        print(f"[ERROR][L0.file] 文件为空: {path}")
        return 1
    issues = validate_text(text, duration, max_refs, lockframe)
    for i in issues:
        print(i.render())
    errs = sum(1 for i in issues if i.level == "ERROR")
    warns = len(issues) - errs
    print(f"— {path.name}: ERROR {errs} / WARN {warns}")
    return 0 if errs == 0 else 1


def selftest() -> int:
    base = Path(__file__).resolve().parent.parent / "samples"
    cases = [
        ("good.md", 0, None),
        ("bad_timestamp.md", 1, "L1.timestamp"),
        ("bad_negative.md", 1, "L2.negative"),
        ("bad_missing_section.md", 1, "L1.sections"),
        ("bad_labels.md", 1, "L1.labels"),
        ("bad_steps.md", 1, "L2.steps"),
    ]
    failed = 0
    for name, want_code, want_rule in cases:
        p = base / name
        if not p.exists():
            print(f"FAIL {name}: 样本缺失")
            failed += 1
            continue
        issues = validate_text(p.read_text(encoding="utf-8"), 10.0, 5, False)
        code = 0 if not any(i.level == "ERROR" for i in issues) else 1
        ok = code == want_code and (want_rule is None or any(i.rule == want_rule for i in issues))
        print(("PASS " if ok else "FAIL ") + name + (
            "" if ok else f" (code={code}, rules={[i.rule for i in issues]})"
        ))
        failed += 0 if ok else 1
    print(f"selftest: {len(cases) - failed}/{len(cases)}")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="prompt validator skeleton")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--max-refs", type=int, default=5)
    ap.add_argument("--lockframe", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.path:
        ap.print_help()
        return 2
    return run_file(Path(a.path), a.duration, a.max_refs, a.lockframe)


if __name__ == "__main__":
    sys.exit(main())
