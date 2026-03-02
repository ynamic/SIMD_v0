"""
LoopAnalyzer.py
---------------
对 CLoopExtraction 提取到的 LoopInfo 进行模式识别和可向量化性分析。

对外唯一入口：
    analyze_loops(loops: List[LoopInfo]) -> List[AnalyzedLoop]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from CLoopExtraction import (
    AccessPattern, ArrayAccess, LoopInfo, OperatorKind
)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class LoopPattern(Enum):
    ELEMENTWISE  = auto()  # a[i] = b[i] op c[i]
    REDUCTION    = auto()  # sum += a[i]
    CONDITIONAL  = auto()  # if(cond) array_op
    UNKNOWN      = auto()  # 不支持的模式


class VectorizabilityStatus(Enum):
    VECTORIZABLE     = auto()
    NOT_VECTORIZABLE = auto()
    PARTIAL          = auto()  # 外层可向量化，内层不可（矩阵 k 层）


class DataType(Enum):
    FLOAT32 = auto()  # float
    FLOAT64 = auto()  # double
    INT32   = auto()  # int / int32_t
    INT64   = auto()  # long / int64_t
    UINT32  = auto()  # unsigned int
    UNKNOWN = auto()


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class DependencyInfo:
    has_loop_carried_dep: bool       # 是否存在循环携带依赖
    dep_type:             str        # "RAW" / "WAR" / "WAW" / "none"
    dep_distance:         Optional[int]
    dep_arrays:           List[str]
    reason:               str        # 可读说明


@dataclass
class AnalyzedLoop:
    """包含原始 LoopInfo + 分析结论"""
    original:         LoopInfo
    pattern:          LoopPattern
    status:           VectorizabilityStatus
    data_type:        DataType
    operator:         OperatorKind
    dependency:       DependencyInfo

    # SVE 代码生成所需
    sve_element_type: str   # "float" / "double" / "int32_t" / "int64_t"
    sve_type_suffix:  str   # "_f32" / "_f64" / "_s32" / "_s64" / "_u32"
    vl_hint:          str   # "svcntw()" / "svcntd()"
    whilelt_suffix:   str   # "b32" / "b64"

    # 不可向量化时的原因
    rejection_reason: Optional[str] = None

    # 归约初始值（从原始代码推断，默认 "0"）
    reduction_init_val: str = '0'


# ---------------------------------------------------------------------------
# 核心分析器
# ---------------------------------------------------------------------------

# C 类型字符串 → DataType 映射
_TYPE_MAP: Dict[str, DataType] = {
    'float':         DataType.FLOAT32,
    'double':        DataType.FLOAT64,
    'int':           DataType.INT32,
    'int32_t':       DataType.INT32,
    'unsigned int':  DataType.UINT32,
    'uint32_t':      DataType.UINT32,
    'long':          DataType.INT64,
    'long long':     DataType.INT64,
    'int64_t':       DataType.INT64,
}

# DataType → SVE 类型信息
_SVE_TYPE_INFO: Dict[DataType, Tuple[str, str, str, str]] = {
    #               (element_type, suffix,  vl_hint,    whilelt)
    DataType.FLOAT32: ('float',    '_f32',  'svcntw()', 'b32'),
    DataType.FLOAT64: ('double',   '_f64',  'svcntd()', 'b64'),
    DataType.INT32:   ('int32_t',  '_s32',  'svcntw()', 'b32'),
    DataType.INT64:   ('int64_t',  '_s64',  'svcntd()', 'b64'),
    DataType.UINT32:  ('uint32_t', '_u32',  'svcntw()', 'b32'),
    DataType.UNKNOWN: ('float',    '_f32',  'svcntw()', 'b32'),  # 默认 float
}


class LoopAnalyzer:

    # ---- 主分析函数 ----

    def analyze(self, loop: LoopInfo) -> AnalyzedLoop:
        pattern = self._classify_pattern(loop)
        dep     = self._check_vectorizability(loop, pattern)

        if dep.has_loop_carried_dep:
            status = VectorizabilityStatus.NOT_VECTORIZABLE
        elif pattern == LoopPattern.UNKNOWN:
            status = VectorizabilityStatus.NOT_VECTORIZABLE
        else:
            status = VectorizabilityStatus.VECTORIZABLE

        dt = self._infer_data_type(loop)
        etype, suffix, vl, wl = _SVE_TYPE_INFO[dt]
        op = loop.body_operator

        # 归约初始值推断（保守默认 "0" / "0.0f"）
        red_init = self._infer_reduction_init(loop, dt)

        return AnalyzedLoop(
            original=loop,
            pattern=pattern,
            status=status,
            data_type=dt,
            operator=op,
            dependency=dep,
            sve_element_type=etype,
            sve_type_suffix=suffix,
            vl_hint=vl,
            whilelt_suffix=wl,
            rejection_reason=dep.reason if status == VectorizabilityStatus.NOT_VECTORIZABLE else None,
            reduction_init_val=red_init,
        )

    # ---- 模式分类 ----

    def _classify_pattern(self, loop: LoopInfo) -> LoopPattern:
        """
        优先级：REDUCTION > CONDITIONAL > ELEMENTWISE > UNKNOWN
        """
        # REDUCTION：存在归约变量
        if loop.is_reduction and loop.reduction_var:
            return LoopPattern.REDUCTION

        # CONDITIONAL：循环体含条件分支
        if loop.has_condition and loop.condition_info:
            return LoopPattern.CONDITIONAL

        # ELEMENTWISE：写操作下标只含当前循环变量
        if self._looks_like_elementwise(loop):
            return LoopPattern.ELEMENTWISE

        return LoopPattern.UNKNOWN

    def _looks_like_elementwise(self, loop: LoopInfo) -> bool:
        """
        逐元素模式判断：
        - 有数组写操作
        - 写下标只含当前循环变量（无额外变量）
        - 无归约
        """
        if not loop.array_writes:
            return False
        lv = loop.loop_var
        for aw in loop.array_writes:
            if not aw.index_vars:
                return False
            # 下标变量集合只包含当前循环变量
            other_vars = [v for v in aw.index_vars if v != lv]
            if other_vars:
                return False
        return True

    # ---- 可向量化性检查 ----

    def _check_vectorizability(self, loop: LoopInfo, pattern: LoopPattern) -> DependencyInfo:
        """
        按规则检查是否存在循环携带依赖或其他向量化障碍。
        """
        # 规则1：非单位步长
        if loop.loop_step == 0:
            return DependencyInfo(
                has_loop_carried_dep=True,
                dep_type='unknown-step',
                dep_distance=None,
                dep_arrays=[],
                reason=f'循环步长未知（非常量步长），无法向量化',
            )
        if loop.loop_step != 1:
            return DependencyInfo(
                has_loop_carried_dep=True,
                dep_type='non-unit-stride',
                dep_distance=None,
                dep_arrays=[],
                reason=f'非单位步长（step={loop.loop_step}），暂不支持向量化',
            )

        # 规则2：WAR 依赖检测（a[i] 写 + a[i+k] 读，k>0：先读后写）
        write_names = {aw.array_name for aw in loop.array_writes}
        for ar in loop.array_reads:
            if ar.array_name in write_names and ar.index_offset > 0:
                return DependencyInfo(
                    has_loop_carried_dep=True,
                    dep_type='WAR',
                    dep_distance=ar.index_offset,
                    dep_arrays=[ar.array_name],
                    reason=(
                        f'数组 {ar.array_name} 存在循环携带 WAR 依赖'
                        f'（读 offset={ar.index_offset}）'
                    ),
                )

        # 规则3：别名检测（写数组出现在读数组中，且非归约）
        if not loop.is_reduction:
            read_names = {ar.array_name for ar in loop.array_reads}
            alias = write_names & read_names
            # 允许 a[i] = f(a[i])（下标完全相同），但禁止 a[i] = f(a[i+k])
            for aw in loop.array_writes:
                if aw.array_name in read_names:
                    # 检查是否有偏移读
                    for ar in loop.array_reads:
                        if ar.array_name == aw.array_name and ar.index_offset != 0:
                            # 正偏移（读后方元素）：WAR；负偏移（读前方元素）：RAW
                            dep_type = 'WAR' if ar.index_offset > 0 else 'RAW'
                            return DependencyInfo(
                                has_loop_carried_dep=True,
                                dep_type=dep_type,
                                dep_distance=abs(ar.index_offset),
                                dep_arrays=[aw.array_name],
                                reason=(
                                    f'数组 {aw.array_name} 存在别名依赖'
                                    f'（写 a[i] 同时读 a[i+{ar.index_offset}]）'
                                ),
                            )

        # 规则4：CONDITIONAL 中条件修改循环控制变量
        if loop.has_condition and loop.condition_info:
            lv = loop.loop_var
            for op_desc in (loop.condition_info.true_branch_ops +
                            loop.condition_info.false_branch_ops):
                # 粗略检查描述字符串中是否含循环变量赋值
                if op_desc.startswith(lv + ' '):
                    return DependencyInfo(
                        has_loop_carried_dep=True,
                        dep_type='control',
                        dep_distance=None,
                        dep_arrays=[],
                        reason=f'条件分支修改循环控制变量 {lv}，无法向量化',
                    )

        return DependencyInfo(
            has_loop_carried_dep=False,
            dep_type='none',
            dep_distance=None,
            dep_arrays=[],
            reason='无循环携带依赖，可向量化',
        )

    # ---- 类型推断 ----

    def _infer_data_type(self, loop: LoopInfo) -> DataType:
        """
        从 type_context 推断主操作数数据类型。
        优先查写数组类型，其次查读数组类型，最后返回 UNKNOWN。
        """
        # 优先查写目标
        for aw in loop.array_writes:
            dt = self._lookup_type(aw.array_name, loop.type_context)
            if dt != DataType.UNKNOWN:
                return dt
        # 其次查读数组
        for ar in loop.array_reads:
            dt = self._lookup_type(ar.array_name, loop.type_context)
            if dt != DataType.UNKNOWN:
                return dt
        # 通过 ArrayAccess.c_type 直接推断
        for acc in loop.array_writes + loop.array_reads:
            if acc.c_type:
                dt = _TYPE_MAP.get(acc.c_type.strip(), DataType.UNKNOWN)
                if dt != DataType.UNKNOWN:
                    return dt
        return DataType.UNKNOWN

    def _lookup_type(self, name: str, ctx: Dict[str, str]) -> DataType:
        raw = ctx.get(name, '')
        if not raw:
            return DataType.UNKNOWN
        # 去掉指针符，取基础类型
        base = raw.rstrip('*').strip()
        # 去掉常见 C 类型修饰符（const / volatile / restrict 等变体）
        # 通过逐词过滤，避免对类型名称本身的误替换
        _QUALIFIERS = frozenset({'const', 'volatile', 'restrict', '__restrict__', '__restrict'})
        tokens = [t for t in base.split() if t not in _QUALIFIERS]
        base = ' '.join(tokens)
        return _TYPE_MAP.get(base, DataType.UNKNOWN)

    # ---- 归约初始值推断 ----

    def _infer_reduction_init(self, loop: LoopInfo, dt: DataType) -> str:
        """根据数据类型和归约算子返回合适的归约初始值字符串"""
        op = loop.reduction_op or loop.body_operator
        if op == OperatorKind.MUL:
            if dt == DataType.FLOAT32:
                return '1.0f'
            if dt == DataType.FLOAT64:
                return '1.0'
            return '1'
        # 加法/减法/其他：加法幺元 0
        if dt in (DataType.FLOAT32,):
            return '0.0f'
        if dt in (DataType.FLOAT64,):
            return '0.0'
        return '0'


# ---------------------------------------------------------------------------
# 模块对外入口
# ---------------------------------------------------------------------------

def analyze_loops(loops: List[LoopInfo]) -> List[AnalyzedLoop]:
    """
    批量分析循环列表，返回 AnalyzedLoop 列表。
    只分析最内层循环（len(inner_loops) == 0）。
    """

    def _walk(loop: LoopInfo) -> List[LoopInfo]:
        nodes = [loop]
        for child in loop.inner_loops:
            nodes.extend(_walk(child))
        return nodes

    analyzer = LoopAnalyzer()
    results: List[AnalyzedLoop] = []
    all_loops: List[LoopInfo] = []
    for root in loops:
        all_loops.extend(_walk(root))

    for loop in all_loops:
        if not loop.inner_loops:
            results.append(analyzer.analyze(loop))

    return results
