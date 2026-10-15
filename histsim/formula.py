# -*- coding: utf-8 -*-
"""受限公式求值器：自定义相似度表达式（白名单 AST，不执行任意代码）。

设计取舍：
* 用户要「相似度定义可选，也可以自定义公式」；直接 ``eval`` 用户输入会让本机
  工具变成任意代码执行入口，因此这里用标准库 ``ast`` 做**白名单遍历**——
  只允许算术运算、白名单函数与三个变量，属性访问/下标/lambda/推导式一律拒绝。
* 变量固定为 ``d``（逐格点差，形状 ``(N, C)``）、``w``（距离权重，形状 ``(C,)``）、
  ``n``（格点数）；``sum``/``mean`` 沿格点轴（``axis=-1``）归约，保证结果是
  「每个样本一个标量」，可直接作为排名依据。
"""
from __future__ import annotations

import ast
from typing import Callable, Dict, Optional

import numpy as np

# 可用变量（供界面展示帮助）
FORMULA_VARS: Dict[str, str] = {
    "d": "逐格点差（历史 − 目标，有符号）",
    "w": "距离权重（与格点同长度）",
    "n": "格点数",
}


def _sum(x):
    """沿格点轴求和：``(N, C) -> (N,)``；标量输入退化为普通求和。"""
    return np.sum(x, axis=-1) if np.ndim(x) == 2 else np.sum(x)


def _mean(x):
    """沿格点轴求均值：``(N, C) -> (N,)``；标量输入退化为普通均值。"""
    return np.mean(x, axis=-1) if np.ndim(x) == 2 else np.mean(x)


# 可用函数（名字 -> (实现, 说明)）
FORMULA_FUNCS: Dict[str, tuple] = {
    "abs": (np.abs, "绝对值"),
    "sqrt": (np.sqrt, "平方根"),
    "exp": (np.exp, "自然指数"),
    "log": (np.log, "自然对数"),
    "log1p": (np.log1p, "log(1+x)"),
    "power": (np.power, "幂 power(x, p)"),
    "clip": (np.clip, "截断 clip(x, lo, hi)"),
    "minimum": (np.minimum, "逐元素取小"),
    "maximum": (np.maximum, "逐元素取大"),
    "sum": (_sum, "沿格点轴求和（每样本一个值）"),
    "mean": (_mean, "沿格点轴求均值（每样本一个值）"),
}

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv,
                   ast.Mod)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)

HINT = ("可用变量：d（逐格点差）、w（距离权重）、n（格点数）；"
        "可用函数：abs、sqrt、exp、log、log1p、power、clip、minimum、"
        "maximum、sum、mean（sum/mean 沿格点轴归约）")


def _walk(node: ast.AST, used: set, errs: list) -> None:
    """只读遍历：把所有不合法节点记成错误，不做任何求值。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            errs.append(f"不支持的常量：{node.value!r}（只允许数字）")
        return
    if isinstance(node, ast.Name):
        if node.id not in FORMULA_VARS:
            errs.append(f"未知变量：{node.id}")
        else:
            used.add(node.id)
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            errs.append(f"不支持的运算符：{type(node.op).__name__}")
        _walk(node.left, used, errs)
        _walk(node.right, used, errs)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            errs.append(f"不支持的一元运算符：{type(node.op).__name__}")
        _walk(node.operand, used, errs)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FORMULA_FUNCS:
            errs.append("只允许调用白名单函数：" + "、".join(FORMULA_FUNCS))
            return
        if node.keywords:
            errs.append("函数不支持关键字参数")
        # 函数名不当作变量遍历（否则会被误判为未知变量）
        for a in node.args:
            if isinstance(a, ast.Starred):
                errs.append("不支持 *args 展开")
                continue
            _walk(a, used, errs)
        return
    errs.append(f"不支持的语法：{type(node).__name__}（禁止属性访问、下标、"
                "lambda、推导式等）")


def check_formula(expr: str) -> Optional[str]:
    """校验表达式，返回错误文本或 ``None``（合法）。"""
    text = (expr or "").strip()
    if not text:
        return "自定义相似度公式不能为空"
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        return f"公式语法错误：{exc.msg}"
    errs: list = []
    used: set = set()
    _walk(tree.body, used, errs)
    if errs:
        return "；".join(dict.fromkeys(errs))
    if not used:
        return "公式至少要引用变量 d 或 w"
    return None


def _eval(node: ast.AST, env: Dict[str, object]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            return a / b
        if isinstance(op, ast.Pow):
            return a ** b
        if isinstance(op, ast.FloorDiv):
            return a // b
        return a % b
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.Call):
        fn = FORMULA_FUNCS[node.func.id][0]  # type: ignore[union-attr]
        return fn(*[_eval(a, env) for a in node.args])
    raise ValueError(f"不支持的语法：{type(node).__name__}")  # pragma: no cover


def compile_formula(expr: str) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """编译为 ``f(d, w) -> si``；形状不对（未归约）时直接抛错。"""
    err = check_formula(expr)
    if err:
        raise ValueError(err)
    tree = ast.parse((expr or "").strip(), mode="eval")

    def fn(d: np.ndarray, w: np.ndarray) -> np.ndarray:
        env = {"d": d, "w": w, "n": np.asarray(d).shape[-1]}
        return np.asarray(_eval(tree, env), dtype=float)

    probe = fn(np.zeros((3, 4)), np.ones(4))
    if probe.shape != (3,):
        raise ValueError("公式必须归约为每个样本一个数值，例如 sqrt(sum(w*d*d))")
    return fn


def _self_check() -> None:
    assert check_formula("sqrt(sum(w*d*d))") is None
    assert check_formula("sum(w*abs(d))") is None
    assert check_formula("sqrt(mean(d*d))") is None
    assert check_formula("sum(d)/n") is None
    # 非法输入必须被拒
    assert check_formula("") is not None
    assert check_formula("__import__('os')") is not None
    assert check_formula("d.x") is not None
    assert check_formula("d[0]") is not None
    assert check_formula("max(d)") is not None
    assert check_formula("lambda x: x") is not None
    assert check_formula("[d for _ in w]") is not None
    assert check_formula("未知变量+1") is not None
    assert check_formula("1+1") is not None  # 未引用变量
    # 求值
    d = np.ones((5, 4))
    w = np.ones(4)
    assert np.allclose(compile_formula("sqrt(sum(w*d*d))")(d, w), 2.0)
    assert np.allclose(compile_formula("sum(w*abs(d))")(d, w), 4.0)
    assert np.allclose(compile_formula("sqrt(mean(d*d))")(d, w), 1.0)
    assert np.allclose(compile_formula("sum(d)/n")(d, w), 1.0)
    # 乘权重与不乘权重必须给出不同结果
    d2 = np.array([[1.0, 3.0]])
    w2 = np.array([1.0, 0.5])
    assert not np.allclose(compile_formula("sum(w*d*d)")(d2, w2),
                           compile_formula("sum(d*d)")(d2, w2))
    # 未归约的公式在校验通过（语法合法）但编译时被拒
    assert check_formula("sqrt(d*d)") is None
    try:
        compile_formula("sqrt(d*d)")
        raise AssertionError("应该拒绝未归约的公式")
    except ValueError as exc:
        assert "归约" in str(exc), exc
    print("formula self-check OK")


if __name__ == "__main__":
    _self_check()