# -*- coding: utf-8 -*-
"""检索配置模型：统一参数模型 + 两组内置模板 + 校验 + 配置指纹。

设计要点（对应方案 6.1）：两项需求不是两个功能分支，而是同一套参数的
两组取值。引擎只认 :class:`SearchConfig`，任何新需求都是新配置。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

from .formula import check_formula

# 要素表：key -> (ERA5 文件 element 串, NC 变量短名, 中文名, 单位, 是否默认标准化)
ELEMENTS: Dict[str, tuple] = {
    "u": ("u_component_of_wind", "u", "U 风", "m/s"),
    "v": ("v_component_of_wind", "v", "V 风", "m/s"),
    "t": ("temperature", "t", "温度", "K"),
    "q": ("specific_humidity", "q", "比湿", "kg/kg"),
    "rh": ("relative_humidity", "r", "相对湿度", "%"),
}

# 6 小时降水窗口共用的锚定偏移（小时，相对检索时次）
DEFAULT_RAIN_WINDOW = [-2, -1, 0, 1, 2, 3]
# 24 小时窗口：当日 22UTC ～ 次日 21UTC
DEFAULT_RAIN_WINDOW_24H = list(range(-2, 22))

# 枚举：供校验与界面下拉共用（key, 中文名）
SAMPLE_MODES: List[Tuple[str, str]] = [
    ("calendar", "日历全量（年 × 月 × 当月每天）"),
    ("same_period", "历史同期（目标日月日 ± x 天，忽略月份）"),
]
FEATURE_PROCESSES: List[Tuple[str, str]] = [
    ("none", "不处理（保留原始量纲）"),
    ("standardize", "逐要素标准化（除以历史标准差）"),
    ("normalize", "逐要素归一化（min-max 到 0–1）"),
]
SIMILARITIES: List[Tuple[str, str]] = [
    ("weighted_abs", "加权绝对差（Σw·|Δx|）"),
    ("weighted_sq", "加权平方差（Σw·Δx²）"),
    ("euclidean", "欧氏距离（√ΣΔx²，不含距离权重）"),
    ("cosine", "余弦距离（1 − cos，不含距离权重）"),
    ("correlation", "相关系数距离（1 − r，不含距离权重）"),
    ("custom", "自定义公式"),
]
_SAMPLE_MODE_KEYS = {k for k, _ in SAMPLE_MODES}
_FEATURE_KEYS = {k for k, _ in FEATURE_PROCESSES}
_SIMILARITY_KEYS = {k for k, _ in SIMILARITIES}


def var_key(element: str, level: float) -> str:
    """要素×层次组合键，如 (u, 925) -> 'u925'。"""
    lv = int(level)
    return f"{element}{lv}"


def split_key(key: str):
    """'rh500' -> ('rh', 500.0)。"""
    i = len(key)
    while i > 0 and key[i - 1].isdigit():
        i -= 1
    return key[:i], float(key[i:])


def var_label(key: str) -> str:
    """'u925' -> 'U 风 925hPa'。"""
    el, lv = split_key(key)
    name = ELEMENTS.get(el, (el, el, el, ""))[2]
    return f"{name} {int(lv)}hPa"


def var_units(key: str) -> str:
    el, _ = split_key(key)
    return ELEMENTS.get(el, ("", "", "", ""))[3]


@dataclass
class SearchConfig:
    """统一检索参数模型（方案 F2.1 / 6.1）。"""

    name: str = "自定义配置"

    # ── 区域 ──────────────────────────────────────────────────────────
    center_lat: float = 23.1652
    center_lon: float = 113.3405
    half_span_deg: float = 1.0  # 矩形半边长（度）: 200km 取 1.0，500km 取 2.5

    # ── 时间域 ────────────────────────────────────────────────────────
    year_from: int = 2000
    year_to: int = 2024
    months: List[int] = field(default_factory=lambda: [4, 5, 6])
    times: List[int] = field(default_factory=lambda: [0])  # UTC 时次
    # 样本选取方式：calendar＝年×月×当月每天（含目标年）；same_period＝历史同期
    # （目标日月日为锚点，各年份 ±period_days 天，跳过目标年、忽略 months）
    sample_mode: str = "calendar"  # calendar | same_period
    period_days: int = 5           # 同期前后 x 天，手工自定义

    # ── 要素×层次 ─────────────────────────────────────────────────────
    variables: List[List[Any]] = field(
        default_factory=lambda: [["u", 925], ["v", 925], ["rh", 500]]
    )

    # ── 距离权重 ──────────────────────────────────────────────────────
    weight_mode: str = "zones"  # zones | gauss
    zones: List[List[float]] = field(default_factory=lambda: [[50.0, 0.7], [100.0, 0.3]])
    gauss_r0_km: float = 50.0
    # 特征处理方式：none＝不处理（保留可选项）；standardize＝逐要素除以历史标准差；
    # normalize＝逐要素 min-max 归一化。后两者用于消除量纲差异（如 rh(%) 主导 u/v(m/s)）。
    feature_process: str = "none"  # none | standardize | normalize

    # ── 排名 ──────────────────────────────────────────────────────────
    # weighted_abs | weighted_sq | euclidean | cosine | correlation | custom
    similarity: str = "weighted_abs"
    similarity_formula: str = ""   # similarity=="custom" 时生效，白名单表达式
    exclude_days: int = 3

    # ── 组合策略 ──────────────────────────────────────────────────────
    inter_group: List[str] = field(default_factory=lambda: ["u925", "v925", "rh500"])
    inter_topn: int = 50
    direct_group: List[str] = field(default_factory=list)
    direct_topn: int = 5
    partial_intersection: bool = False
    partial_min_hits: int = 2

    # ── 出图选项 ──────────────────────────────────────────────────────
    # 直取要素场图单独出图（不挤占交集要素场图），默认关闭
    plot_direct_fields: bool = False

    # ── 降水产品 ──────────────────────────────────────────────────────
    rain_window: List[int] = field(default_factory=lambda: list(DEFAULT_RAIN_WINDOW))
    rain_window_24h: List[int] = field(default_factory=lambda: list(DEFAULT_RAIN_WINDOW_24H))
    rain_thresholds: List[float] = field(default_factory=lambda: [0.1, 50.0, 100.0])
    enable_24h: bool = True
    point_lat: float = 23.1652
    point_lon: float = 113.3405

    target_date: str = ""  # 'YYYY-MM-DD'

    # ── 派生 ──────────────────────────────────────────────────────────
    @property
    def variable_keys(self) -> List[str]:
        return [var_key(e, l) for e, l in self.variables]

    @property
    def radius_km(self) -> float:
        """矩形的粗估外接尺度，仅用于展示与权重分区说明。"""
        return round(self.half_span_deg * 111.0, 1)

    def levels_of(self, element: str) -> List[float]:
        return sorted({float(l) for e, l in self.variables if e == element})

    def elements(self) -> List[str]:
        seen, out = set(), []
        for e, _ in self.variables:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out

    # ── 校验 / 指纹 / 序列化 ──────────────────────────────────────────
    def validate(self) -> List[str]:
        errs: List[str] = []
        keys = set(self.variable_keys)
        unknown = [k for k in set(self.inter_group) | set(self.direct_group) if k not in keys]
        if unknown:
            errs.append("组合策略引用了未勾选的要素: " + ", ".join(sorted(unknown)))
        if not self.variables:
            errs.append("至少需要勾选一个要素×层次组合")
        if not self.months:
            errs.append("月份不能为空")
        if not self.times:
            errs.append("检索时次不能为空")
        if self.year_from > self.year_to:
            errs.append("起始年份不能大于结束年份")
        if self.sample_mode not in _SAMPLE_MODE_KEYS:
            errs.append("样本选取方式必须是 calendar 或 same_period")
        if not 0 <= self.period_days <= 60:
            errs.append("同期前后天数必须在 0 ～ 60 之间")
        if self.sample_mode == "same_period" and not self.target_date:
            errs.append("历史同期模式必须先指定目标日期（作为同期锚点）")
        if self.feature_process not in _FEATURE_KEYS:
            errs.append("特征处理方式必须是 none、standardize 或 normalize")
        if self.similarity not in _SIMILARITY_KEYS:
            errs.append("相似度度量取值不合法")
        if self.similarity == "custom":
            ferr = check_formula(self.similarity_formula)
            if ferr:
                errs.append("自定义相似度公式： " + ferr)
        if self.inter_group and self.inter_topn < 1:
            errs.append("交集 TopN 必须 ≥ 1")
        if self.direct_group and self.direct_topn < 1:
            errs.append("直取 TopN 必须 ≥ 1")
        if not self.inter_group and not self.direct_group:
            errs.append("交集组与直取组不能同时为空")
        if self.partial_intersection and not (1 <= self.partial_min_hits <= len(self.inter_group)):
            errs.append("部分交集命中数必须在 1 ～ 交集组要素数之间")
        if not self.rain_window:
            errs.append("降水窗口不能为空")
        for e in self.elements():
            if e not in ELEMENTS:
                errs.append(f"未知要素: {e}")
        return errs

    def fingerprint(self) -> str:
        """计算影响检索结果的参数指纹（排除展示名与目标日期）。"""
        payload = asdict(self)
        for k in ("name", "target_date"):
            payload.pop(k, None)
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["variable_keys"] = self.variable_keys
        d["fingerprint"] = self.fingerprint()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SearchConfig":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (d or {}).items() if k in allowed}
        cfg = cls(**clean)
        cfg.variables = [list(v) for v in cfg.variables]
        return cfg

    def copy(self, **overrides) -> "SearchConfig":
        cfg = copy.deepcopy(self)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg


# ── 内置模板（两项需求固化为参数）────────────────────────────────────
def builtin_template(key: str) -> SearchConfig:
    if key == "plan_A":
        return SearchConfig(
            name="暖区降水检索",
            half_span_deg=1.0,
            year_from=2000,
            year_to=2024,
            months=[4, 5, 6],
            times=[0],
            sample_mode="calendar",
            period_days=5,
            variables=[["u", 925], ["v", 925], ["rh", 500]],
            weight_mode="zones",
            zones=[[50.0, 0.7], [100.0, 0.3]],
            feature_process="none",
            similarity="weighted_abs",
            exclude_days=3,
            inter_group=["u925", "v925", "rh500"],
            inter_topn=50,
            direct_group=[],
            direct_topn=5,
            partial_intersection=False,
            plot_direct_fields=False,
            rain_window=[-2, -1, 0, 1, 2, 3],
            rain_thresholds=[0.1, 50.0, 100.0],
            enable_24h=False,
        )
    if key == "plan_B":
        return SearchConfig(
            name="锋面热动力检索",
            half_span_deg=2.5,
            year_from=2000,
            year_to=2024,
            months=[2, 3, 4, 5, 6],
            times=[0, 6, 12, 18],
            sample_mode="calendar",
            period_days=5,
            variables=[
                ["u", 975], ["u", 925], ["u", 850],
                ["v", 975], ["v", 925], ["v", 850],
                ["t", 975], ["t", 925], ["t", 850],
                ["q", 975], ["q", 925], ["q", 850],
            ],
            weight_mode="zones",
            zones=[[50.0, 0.7], [100.0, 0.3]],
            feature_process="standardize",
            similarity="weighted_abs",
            exclude_days=3,
            inter_group=["v975", "v925", "v850"],
            inter_topn=60,
            direct_group=["u975", "u925", "u850", "t975", "t925", "t850",
                          "q975", "q925", "q850"],
            direct_topn=5,
            partial_intersection=False,
            plot_direct_fields=False,
            rain_window=[-2, -1, 0, 1, 2, 3],
            rain_thresholds=[0.1, 50.0],
            enable_24h=True,
        )
    raise KeyError(f"未知内置模板: {key}")


BUILTIN_KEYS = ["plan_A", "plan_B"]


def builtin_templates() -> Dict[str, SearchConfig]:
    return {k: builtin_template(k) for k in BUILTIN_KEYS}


def _self_check() -> None:
    a, b = builtin_template("plan_A"), builtin_template("plan_B")
    assert not a.validate(), a.validate()
    assert not b.validate(), b.validate()
    assert a.variable_keys == ["u925", "v925", "rh500"]
    assert len(b.variable_keys) == 12
    assert split_key("rh500") == ("rh", 500.0) and var_key("rh", 500) == "rh500"
    assert set(b.inter_group) | set(b.direct_group) == set(b.variable_keys)
    # 指纹应随参数变化、与展示名无关
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() == a.copy(name="换了个名字").fingerprint()
    assert a.fingerprint() != a.copy(inter_topn=42).fingerprint()
    assert not SearchConfig.from_dict(a.to_dict() | {"无关字段": 1}).validate()
    # V2.0.0 新字段：默认值、枚举校验、自定义公式校验
    assert SearchConfig().sample_mode == "calendar"
    assert SearchConfig().period_days == 5
    assert SearchConfig().feature_process == "none"
    assert SearchConfig().similarity_formula == "" and not SearchConfig().plot_direct_fields
    assert a.feature_process == "none" and b.feature_process == "standardize"
    assert a.fingerprint() != a.copy(feature_process="normalize").fingerprint()
    assert a.fingerprint() != a.copy(sample_mode="same_period").fingerprint()
    assert a.fingerprint() != a.copy(plot_direct_fields=True).fingerprint()
    assert any("样本选取方式" in e for e in a.copy(sample_mode="乱填").validate())
    assert any("同期前后天数" in e for e in a.copy(period_days=99).validate())
    assert any("特征处理方式" in e for e in a.copy(feature_process="乱填").validate())
    assert any("相似度度量" in e for e in a.copy(similarity="乱填").validate())
    # 同期模式必须有目标日期（作为锚点）
    assert any("目标日期" in e for e in a.copy(sample_mode="same_period").validate())
    assert not a.copy(sample_mode="same_period", target_date="2024-06-15").validate()
    # 自定义公式：合法通过、非法被拒
    assert not a.copy(similarity="custom",
                      similarity_formula="sqrt(sum(w*d*d))").validate()
    assert any("自定义相似度公式" in e
               for e in a.copy(similarity="custom",
                               similarity_formula="__import__('os')").validate())
    assert any("自定义相似度公式" in e
               for e in a.copy(similarity="custom", similarity_formula="").validate())
    # standardize 字段已删除：旧字段不再出现在模型里
    assert "standardize" not in SearchConfig.__dataclass_fields__
    print("config self-check OK")


if __name__ == "__main__":
    _self_check()