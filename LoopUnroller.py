"""
LoopUnroller.py
---------------
循环展开决策模块。

职责：
  - 对 AnalyzedLoop 列表分析其特征，决定展开因子和预取距离
  - 输出 UnrollConfig，由 SVECodeGen 消费以生成三段式展开代码

数据流位置：
    analyze_loops() → [LoopUnroller] → unroll_loops() → List[(AnalyzedLoop, UnrollConfig)]
                                                              ↓
                                                        SVECodeGen.generate()

对外入口：
    unroll_loops(analyzed: List[AnalyzedLoop], **kwargs) -> List[Tuple[AnalyzedLoop, UnrollConfig]]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from LoopAnalyzer import AnalyzedLoop, LoopPattern, VectorizabilityStatus


# ---------------------------------------------------------------------------
# 展开配置数据类
# ---------------------------------------------------------------------------

@dataclass
class UnrollConfig:
    """
    描述单个循环的展开策略，由 SVECodeGen 用于生成三段式代码。

    三段式结构：
      ┌─ 主循环   (i + unroll_factor*vl ≤ n)  svptrue + unroll_factor 份展开 + 可选预取
      ├─ 清理循环 (i + vl ≤ n)               svptrue + 1 份，不展开
      └─ 尾部     (i < n)                    svwhilelt 最多执行 1 次

    字段说明：
      unroll_factor    主循环每次处理的向量数，1 表示不展开（退化为双段式：svptrue主循环+svwhilelt尾部）
      enable_prefetch  是否在主循环插入 svprfd 软件预取
      prefetch_dist    预取距离，单位为 vl（建议 4~16，取决于内存带宽和 L1 大小）
      reason           展开决策的可读说明，用于 --verbose 输出
    """
    unroll_factor:    int   = 1
    enable_prefetch:  bool  = False
    prefetch_dist:    int   = 8
    reason:           str   = ''


# ---------------------------------------------------------------------------
# 展开因子决策规则
# ---------------------------------------------------------------------------
#
# 规则优先级（从高到低）：
#   1. 不可向量化的循环 → unroll_factor=1, enable_prefetch=False（后续由 fallback 处理）
#   2. REDUCTION → unroll_factor=1（归约累加器有顺序依赖，不展开以保证 _m 语义）
#   3. MATRIX    → unroll_factor=2（向量化 j 循环，展开 2 份提升 ILP）
#   4. ELEMENTWISE，读数组 ≥ 2 → unroll_factor=2, enable_prefetch=True
#      （典型 a[i]=b[i]+c[i]，访存密集，预取收益明显）
#   5. ELEMENTWISE，读数组 == 1（纯赋值/单操作数）→ unroll_factor=2, enable_prefetch=False
#   6. 其他 VECTORIZABLE → unroll_factor=1

_DEFAULT_PREFETCH_DIST = 8   # 默认 8*vl 预取距离


def _decide(loop: AnalyzedLoop) -> UnrollConfig:
    """对单个 AnalyzedLoop 确定 UnrollConfig。"""

    # 不可向量化：不展开，SVECodeGen 会走 fallback 路径
    if loop.status == VectorizabilityStatus.NOT_VECTORIZABLE:
        return UnrollConfig(
            unroll_factor=1,
            enable_prefetch=False,
            reason='不可向量化，跳过展开',
        )

    pattern = loop.pattern
    n_reads = len(loop.original.array_reads)

    if pattern == LoopPattern.REDUCTION:
        return UnrollConfig(
            unroll_factor=1,
            enable_prefetch=False,
            reason='REDUCTION：累加器有序依赖，不展开',
        )

    if pattern == LoopPattern.MATRIX:
        return UnrollConfig(
            unroll_factor=2,
            enable_prefetch=True,
            prefetch_dist=_DEFAULT_PREFETCH_DIST,
            reason='MATRIX：内层 j 循环展开 2 份，开启预取',
        )

    if pattern == LoopPattern.ELEMENTWISE:
        if n_reads >= 2:
            return UnrollConfig(
                unroll_factor=2,
                enable_prefetch=True,
                prefetch_dist=_DEFAULT_PREFETCH_DIST,
                reason=f'ELEMENTWISE：{n_reads} 个读数组，展开 2 份 + 软件预取',
            )
        else:
            return UnrollConfig(
                unroll_factor=2,
                enable_prefetch=False,
                reason='ELEMENTWISE：单读数组，展开 2 份，不预取',
            )

    # CONDITIONAL / UNKNOWN / 其他
    return UnrollConfig(
        unroll_factor=1,
        enable_prefetch=False,
        reason=f'{pattern.name}：保持 unroll=1',
    )


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def unroll_loops(
    analyzed: List[AnalyzedLoop],
    unroll_factor: int  = 0,   # 0 = 自动决策；>0 = 强制覆盖所有循环的展开因子
    prefetch_dist: int  = 0,   # 0 = 自动决策；>0 = 强制覆盖预取距离
    enable_prefetch: bool | None = None,  # None = 自动；True/False = 强制覆盖
) -> List[Tuple[AnalyzedLoop, UnrollConfig]]:
    """
    对分析后的循环列表批量决定展开配置。

    参数：
        analyzed         来自 analyze_loops() 的结果列表
        unroll_factor    强制覆盖展开因子（CLI --unroll 选项使用）
        prefetch_dist    强制覆盖预取距离（CLI --prefetch-dist 选项使用）
        enable_prefetch  强制覆盖是否预取

    返回：
        List[(AnalyzedLoop, UnrollConfig)]，与 analyzed 等长一一对应
    """
    result: List[Tuple[AnalyzedLoop, UnrollConfig]] = []
    for loop in analyzed:
        cfg = _decide(loop)

        # 应用强制覆盖
        if unroll_factor > 0:
            cfg.unroll_factor = unroll_factor
            cfg.reason += f'（CLI 强制 unroll={unroll_factor}）'
        if prefetch_dist > 0:
            cfg.prefetch_dist = prefetch_dist
        if enable_prefetch is not None:
            cfg.enable_prefetch = enable_prefetch

        result.append((loop, cfg))
    return result
