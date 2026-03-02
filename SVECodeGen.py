"""
SVECodeGen.py
-------------
根据 LoopAnalyzer 的分析结果，生成对应的 ARM SVE intrinsics C 代码片段。

对外唯一入口：
    generate_sve_code(loop: AnalyzedLoop) -> str
"""

from __future__ import annotations

from textwrap import indent
from typing import Dict, List, Optional

from CLoopExtraction import ArrayAccess, LoopInfo, OperatorKind
from LoopAnalyzer import AnalyzedLoop, DataType, LoopPattern, VectorizabilityStatus
from LoopUnroller import UnrollConfig


# ---------------------------------------------------------------------------
# Intrinsics 映射表
# ---------------------------------------------------------------------------

# 算术 intrinsics（_x 形式，非谓词保护区域不确定值，适合纯计算）
_ARITH_X: Dict[OperatorKind, Dict[str, str]] = {
    OperatorKind.ADD: {
        '_f32': 'svadd_f32_x', '_f64': 'svadd_f64_x',
        '_s32': 'svadd_s32_x', '_s64': 'svadd_s64_x', '_u32': 'svadd_u32_x',
    },
    OperatorKind.SUB: {
        '_f32': 'svsub_f32_x', '_f64': 'svsub_f64_x',
        '_s32': 'svsub_s32_x', '_s64': 'svsub_s64_x', '_u32': 'svsub_u32_x',
    },
    OperatorKind.MUL: {
        '_f32': 'svmul_f32_x', '_f64': 'svmul_f64_x',
        '_s32': 'svmul_s32_x', '_s64': 'svmul_s64_x', '_u32': 'svmul_u32_x',
    },
    OperatorKind.DIV: {
        '_f32': 'svdiv_f32_x', '_f64': 'svdiv_f64_x',
        '_s32': 'svdiv_s32_x', '_s64': 'svdiv_s64_x',
    },
    OperatorKind.ASSIGN: {
        '_f32': '', '_f64': '', '_s32': '', '_s64': '', '_u32': '',
    },
}

# 算术 intrinsics（_m 形式，merge，用于归约累加）
_ARITH_M: Dict[OperatorKind, Dict[str, str]] = {
    OperatorKind.ADD: {
        '_f32': 'svadd_f32_m', '_f64': 'svadd_f64_m',
        '_s32': 'svadd_s32_m', '_s64': 'svadd_s64_m', '_u32': 'svadd_u32_m',
    },
    OperatorKind.SUB: {
        '_f32': 'svsub_f32_m', '_f64': 'svsub_f64_m',
        '_s32': 'svsub_s32_m', '_s64': 'svsub_s64_m',
    },
    OperatorKind.MUL: {
        '_f32': 'svmul_f32_m', '_f64': 'svmul_f64_m',
        '_s32': 'svmul_s32_m', '_s64': 'svmul_s64_m',
    },
}

# 加载/存储
_LOAD: Dict[str, str] = {
    '_f32': 'svld1_f32', '_f64': 'svld1_f64',
    '_s32': 'svld1_s32', '_s64': 'svld1_s64', '_u32': 'svld1_u32',
}
_STORE: Dict[str, str] = {
    '_f32': 'svst1_f32', '_f64': 'svst1_f64',
    '_s32': 'svst1_s32', '_s64': 'svst1_s64', '_u32': 'svst1_u32',
}

# 归约
_REDUCE: Dict[OperatorKind, Dict[str, str]] = {
    OperatorKind.ADD: {
        '_f32': 'svaddv_f32', '_f64': 'svaddv_f64',
        '_s32': 'svaddv_s32', '_s64': 'svaddv_s64',
    },
}

# 乘加 FMA（矩阵用）
_MLA: Dict[str, str] = {
    '_f32': 'svmla_f32_x', '_f64': 'svmla_f64_x',
    '_s32': 'svmla_s32_x', '_s64': 'svmla_s64_x',
}

# 比较
_CMP: Dict[str, Dict[str, str]] = {
    '>':  {'_f32': 'svcmpgt_f32', '_f64': 'svcmpgt_f64', '_s32': 'svcmpgt_s32', '_s64': 'svcmpgt_s64'},
    '<':  {'_f32': 'svcmplt_f32', '_f64': 'svcmplt_f64', '_s32': 'svcmplt_s32', '_s64': 'svcmplt_s64'},
    '>=': {'_f32': 'svcmpge_f32', '_f64': 'svcmpge_f64', '_s32': 'svcmpge_s32', '_s64': 'svcmpge_s64'},
    '<=': {'_f32': 'svcmple_f32', '_f64': 'svcmple_f64', '_s32': 'svcmple_s32', '_s64': 'svcmple_s64'},
    '==': {'_f32': 'svcmpeq_f32', '_f64': 'svcmpeq_f64', '_s32': 'svcmpeq_s32', '_s64': 'svcmpeq_s64'},
    '!=': {'_f32': 'svcmpne_f32', '_f64': 'svcmpne_f64', '_s32': 'svcmpne_s32', '_s64': 'svcmpne_s64'},
}

# 广播
_DUP: Dict[str, str] = {
    '_f32': 'svdup_f32', '_f64': 'svdup_f64',
    '_s32': 'svdup_s32', '_s64': 'svdup_s64', '_u32': 'svdup_u32',
}

# SVE 向量类型
_VEC_TYPE: Dict[str, str] = {
    '_f32': 'svfloat32_t', '_f64': 'svfloat64_t',
    '_s32': 'svint32_t',   '_s64': 'svint64_t', '_u32': 'svuint32_t',
}

# whilelt 谓词生成（suffix → 函数名）
_WHILELT: Dict[str, str] = {
    'b32': 'svwhilelt_b32',
    'b64': 'svwhilelt_b64',
}

REQUIRED_HEADERS = ['<arm_sve.h>', '<stdint.h>']


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _ind(text: str, level: int = 1, width: int = 4) -> str:
    """缩进辅助"""
    return indent(text, ' ' * width * level)


def _build_zero_literal(dt: DataType) -> str:
    if dt == DataType.FLOAT32:
        return '0.0f'
    if dt == DataType.FLOAT64:
        return '0.0'
    return '0'


def _format_array_ptr(array_name: str, index_var: str, offset: int = 0) -> str:
    """生成数组元素指针表达式，如 &a[i] 或 &a[i + 2]"""
    if offset == 0:
        return f'&{array_name}[{index_var}]'
    if offset > 0:
        return f'&{array_name}[{index_var} + {offset}]'
    return f'&{array_name}[{index_var} - {abs(offset)}]'


def _get_primary_write(loop: LoopInfo) -> Optional[ArrayAccess]:
    """返回主要写数组（第一个）"""
    return loop.array_writes[0] if loop.array_writes else None


def _get_read_arrays(loop: LoopInfo, exclude_name: Optional[str] = None) -> List[ArrayAccess]:
    """返回读数组列表，可排除指定名称"""
    return [ar for ar in loop.array_reads if ar.array_name != exclude_name]


def _condition_val_to_sve(cond_val: str, suffix: str) -> str:
    """将条件右侧值转换为 SVE 广播表达式"""
    dup = _DUP.get(suffix, 'svdup_f32')
    # 检查是否为纯数字/浮点字面量
    try:
        float(cond_val.rstrip('f').rstrip('F'))
        return f'{dup}({cond_val})'
    except ValueError:
        # 变量名，直接广播
        return f'{dup}({cond_val})'


# ---------------------------------------------------------------------------
# 代码生成器
# ---------------------------------------------------------------------------

class SVECodeGen:

    def generate(self, loop: AnalyzedLoop,
                 cfg: Optional[UnrollConfig] = None) -> str:
        """根据模式分派到对应生成函数，cfg 由 LoopUnroller 提供"""
        if cfg is None:
            cfg = UnrollConfig()   # 默认：unroll=1, 无预取

        if loop.status == VectorizabilityStatus.NOT_VECTORIZABLE:
            return self._gen_fallback(loop)

        dispatch = {
            LoopPattern.ELEMENTWISE:  self._gen_elementwise,
            LoopPattern.REDUCTION:    self._gen_reduction,
            LoopPattern.CONDITIONAL:  self._gen_conditional,
            LoopPattern.MATRIX:       self._gen_matrix,
        }
        gen_fn = dispatch.get(loop.pattern, self._gen_fallback)
        # 将 cfg 传给支持展开的生成函数
        if loop.pattern in (LoopPattern.ELEMENTWISE, LoopPattern.REDUCTION):
            return gen_fn(loop, cfg)
        return gen_fn(loop)

    # ------------------------------------------------------------------ #
    #  ELEMENTWISE: a[i] = b[i] op c[i]                                   #
    #  三段式：主循环(svptrue+展开) + 清理循环 + 尾部(svwhilelt 最多1次)  #
    # ------------------------------------------------------------------ #

    def _one_vec_block(
        self,
        loop: AnalyzedLoop,
        pg_var: str,         # 使用的谓词变量名，如 "pg_all" 或 "pg"
        offset_expr: str,    # 当前 i 的表达式，如 "i" 或 "i + vl"
        suffix: str,         # 变量名后缀，用于展开时区分，如 "" 或 "_1"
    ) -> List[str]:
        """生成一份完整的 load → compute → store 指令序列（供展开复用）"""
        orig = loop.original
        suf = loop.sve_type_suffix
        vtype = _VEC_TYPE.get(suf, 'svfloat32_t')
        load_fn = _LOAD.get(suf, 'svld1_f32')
        store_fn = _STORE.get(suf, 'svst1_f32')
        op_kind = orig.body_operator
        arith_fn = _ARITH_X.get(op_kind, {}).get(suf, '')
        write_acc = _get_primary_write(orig)
        read_accs = _get_read_arrays(orig)

        lines: List[str] = []
        vec_read_vars: List[str] = []

        # 加载所有读数组
        for ar in read_accs:
            vname = f'v{ar.array_name}{suffix}'
            if ar.index_offset == 0:
                ptr = f'&{ar.array_name}[{offset_expr}]'
            elif ar.index_offset > 0:
                ptr = f'&{ar.array_name}[{offset_expr} + {ar.index_offset}]'
            else:
                ptr = f'&{ar.array_name}[{offset_expr} - {abs(ar.index_offset)}]'
            lines.append(f'{vtype} {vname} = {load_fn}({pg_var}, {ptr});')
            vec_read_vars.append(vname)

        if not write_acc:
            return lines

        dst_var = f'v{write_acc.array_name}{suffix}'

        # 计算
        if op_kind == OperatorKind.ASSIGN and len(vec_read_vars) == 1:
            lines.append(f'{vtype} {dst_var} = {vec_read_vars[0]};')
        elif arith_fn and len(vec_read_vars) >= 2:
            lines.append(
                f'{vtype} {dst_var} = {arith_fn}({pg_var}, {vec_read_vars[0]}, {vec_read_vars[1]});'
            )
        elif arith_fn and len(vec_read_vars) == 1:
            # a[i] op= b[i] 形式，写数组本身也是读数组
            write_read = next(
                (ar for ar in orig.array_reads if ar.array_name == write_acc.array_name),
                None,
            )
            if write_read:
                lines.append(f'{vtype} {dst_var}_old = {load_fn}({pg_var}, &{write_acc.array_name}[{offset_expr}]);')
                lines.append(f'{vtype} {dst_var} = {arith_fn}({pg_var}, {dst_var}_old, {vec_read_vars[0]});')
            else:
                lines.append(f'{vtype} {dst_var} = {vec_read_vars[0]};')
        else:
            src = vec_read_vars[0] if vec_read_vars else f'svdup_{suf.lstrip("_")}(0)'
            lines.append(f'{vtype} {dst_var} = {src};  /* unknown op */')

        # 存储
        lines.append(f'{store_fn}({pg_var}, &{write_acc.array_name}[{offset_expr}], {dst_var});')
        return lines

    def _gen_elementwise(self, loop: AnalyzedLoop,
                         cfg: Optional[UnrollConfig] = None) -> str:
        if cfg is None:
            cfg = UnrollConfig()
        U  = max(1, cfg.unroll_factor)
        PD = cfg.prefetch_dist

        orig = loop.original
        suf = loop.sve_type_suffix
        whilelt_fn = _WHILELT.get(loop.whilelt_suffix, 'svwhilelt_b32')
        ptrue_fn   = f'svptrue_{loop.whilelt_suffix}()'
        vl_hint    = loop.vl_hint
        lv         = orig.loop_var
        end        = orig.loop_end
        end_expr   = f'(int64_t)({end}) + 1' if orig.loop_end_op == '<=' else f'(int64_t)({end})'
        read_accs  = _get_read_arrays(orig)

        op_name = orig.body_operator.name
        hdr = (f'/* SVE vectorized: ELEMENTWISE {op_name} {loop.sve_element_type}'
               f' | unroll={U} prefetch={PD if cfg.enable_prefetch else "off"} */\n{{')

        body: List[str] = []
        body.append(f'int64_t vl  = {vl_hint};')
        body.append(f'int64_t {lv} = {orig.loop_start};')
        body.append(f'svbool_t pg_all = {ptrue_fn};  /* 全真谓词，主/清理循环复用 */')

        # ── 主循环 ─────────────────────────────────────────────────────────
        if U > 1:
            body.append(f'/* 主循环：{U}×vl 元素/迭代，svptrue 无谓词计算开销 */')
            body.append(f'for (; {lv} + {U}*vl <= {end_expr}; {lv} += {U}*vl) {{')
        else:
            body.append(f'/* 主循环：1×vl 元素/迭代，svptrue 无谓词计算开销 */')
            body.append(f'for (; {lv} + vl <= {end_expr}; {lv} += vl) {{')

        main_inner: List[str] = []
        # 软件预取（对每个读数组插入 svprfd）
        if cfg.enable_prefetch:
            for ar in read_accs:
                main_inner.append(
                    f'svprfd(pg_all, &{ar.array_name}[{lv} + {PD}*vl], SV_PLDL1KEEP);'
                )
        # 展开 U 份 load→compute→store
        for u in range(U):
            offset_expr_u = f'{lv} + {u}*vl' if u > 0 else lv
            suffix_u = f'_{u}' if U > 1 else ''
            main_inner.extend(self._one_vec_block(loop, 'pg_all', offset_expr_u, suffix_u))

        body.append(_ind('\n'.join(main_inner)))
        body.append('}')

        # ── 清理循环（仅在 U>1 时需要，处理主循环展开未覆盖的完整向量）──
        if U > 1:
            body.append(f'/* 清理循环：处理展开余量（最多 {U-1} 次），仍用 svptrue */')
            body.append(f'for (; {lv} + vl <= {end_expr}; {lv} += vl) {{')
            clean_inner = self._one_vec_block(loop, 'pg_all', lv, '')
            body.append(_ind('\n'.join(clean_inner)))
            body.append('}')

        # ── 尾部：svwhilelt 最多执行 1 次 ─────────────────────────────────
        body.append(f'/* 尾部：svwhilelt 最多执行 1 次，处理不足一个向量的剩余元素 */')
        body.append(f'if ({lv} < {end_expr}) {{')
        tail_inner: List[str] = [f'svbool_t pg = {whilelt_fn}({lv}, {end_expr});']
        tail_inner.extend(self._one_vec_block(loop, 'pg', lv, ''))
        body.append(_ind('\n'.join(tail_inner)))
        body.append('}')

        return hdr + '\n' + _ind('\n'.join(body)) + '\n}'

    # ------------------------------------------------------------------ #
    #  REDUCTION: sum += a[i]                                              #
    # ------------------------------------------------------------------ #

    def _gen_reduction(self, loop: AnalyzedLoop,
                       cfg: Optional[UnrollConfig] = None) -> str:
        orig = loop.original
        suf = loop.sve_type_suffix
        vtype = _VEC_TYPE.get(suf, 'svfloat32_t')
        load_fn = _LOAD.get(suf, 'svld1_f32')
        whilelt_fn = _WHILELT.get(loop.whilelt_suffix, 'svwhilelt_b32')
        dup_fn = _DUP.get(suf, 'svdup_f32')
        red_op = orig.reduction_op or OperatorKind.ADD
        arith_m_fn = _ARITH_M.get(red_op, {}).get(suf, f'svadd{suf}_m')
        reduce_fn = _REDUCE.get(red_op, {}).get(suf, f'svaddv{suf}')

        lv = orig.loop_var
        end = orig.loop_end
        end_expr = f'(int64_t)({end}) + 1' if orig.loop_end_op == '<=' else f'(int64_t)({end})'
        vl_hint = loop.vl_hint
        red_var = orig.reduction_var or 'sum'
        init_val = loop.reduction_init_val

        read_acc = next((ar for ar in orig.array_reads), None)
        if read_acc is None:
            return self._gen_fallback(loop)

        lines: List[str] = []
        lines.append(f'/* SVE vectorized: REDUCTION {red_op.name} {loop.sve_element_type} */\n{{')

        body = []
        body.append(f'{vtype} vred = {dup_fn}({init_val});  /* 向量累加器 */')
        body.append(f'int64_t {lv} = {orig.loop_start};')
        body.append(f'int64_t sve_vl = {vl_hint};')
        body.append(f'svbool_t pg;')
        body.append(f'for (; {lv} < {end_expr}; {lv} += sve_vl) {{')

        inner = []
        inner.append(f'pg = {whilelt_fn}({lv}, {end_expr});')
        ptr = _format_array_ptr(read_acc.array_name, lv, read_acc.index_offset)
        inner.append(f'{vtype} va = {load_fn}(pg, {ptr});')
        inner.append(f'vred = {arith_m_fn}(pg, vred, va);  /* merge: 非活跃通道保持 vred */')

        body.append(_ind('\n'.join(inner)))
        body.append('}')
        body.append(f'{red_var} = {reduce_fn}(svptrue_b32(), vred);  /* 水平归约到标量 */')

        lines.append(_ind('\n'.join(body)))
        lines.append('}')

        return '\n'.join(lines)

    # ------------------------------------------------------------------ #
    #  CONDITIONAL: if(a[i] > 0) b[i] = a[i]                             #
    # ------------------------------------------------------------------ #

    def _gen_conditional(self, loop: AnalyzedLoop) -> str:
        orig = loop.original
        suf = loop.sve_type_suffix
        vtype = _VEC_TYPE.get(suf, 'svfloat32_t')
        load_fn = _LOAD.get(suf, 'svld1_f32')
        store_fn = _STORE.get(suf, 'svst1_f32')
        whilelt_fn = _WHILELT.get(loop.whilelt_suffix, 'svwhilelt_b32')

        lv = orig.loop_var
        end = orig.loop_end
        end_expr = f'(int64_t)({end}) + 1' if orig.loop_end_op == '<=' else f'(int64_t)({end})'
        vl_hint = loop.vl_hint

        cinfo = orig.condition_info
        cond_op = cinfo.condition_op if cinfo else '>'
        cond_val = cinfo.condition_val if cinfo else '0'
        cond_var = cinfo.condition_var if cinfo else ''

        cmp_fn = _CMP.get(cond_op, {}).get(suf, f'svcmpgt{suf}')
        cond_sve_val = _condition_val_to_sve(cond_val, suf)

        # 找条件数组（被比较的数组）
        cond_arr_acc = next(
            (ar for ar in orig.array_reads if ar.array_name == cond_var),
            orig.array_reads[0] if orig.array_reads else None,
        )
        # 找写数组
        write_acc = _get_primary_write(orig)

        lines: List[str] = []
        has_else = bool(cinfo and cinfo.false_branch_ops)
        lines.append(f'/* SVE vectorized: CONDITIONAL ({cond_var}[i]{cond_op}{cond_val}) {loop.sve_element_type} */\n{{')

        body = []
        body.append(f'int64_t {lv} = {orig.loop_start};')
        body.append(f'int64_t sve_vl = {vl_hint};')
        body.append(f'svbool_t pg, cond_pg;')
        body.append(f'for (; {lv} < {end_expr}; {lv} += sve_vl) {{')

        inner = []
        inner.append(f'pg = {whilelt_fn}({lv}, {end_expr});')

        if cond_arr_acc:
            ptr = _format_array_ptr(cond_arr_acc.array_name, lv, cond_arr_acc.index_offset)
            inner.append(f'{vtype} v{cond_arr_acc.array_name} = {load_fn}(pg, {ptr});')
            inner.append(
                f'cond_pg = {cmp_fn}(pg, v{cond_arr_acc.array_name}, {cond_sve_val});'
                f'  /* if({cond_var}[i]{cond_op}{cond_val}) */'
            )

        # 加载其他读数组
        for ar in orig.array_reads:
            if cond_arr_acc and ar.array_name == cond_arr_acc.array_name:
                continue
            ptr = _format_array_ptr(ar.array_name, lv, ar.index_offset)
            inner.append(f'{vtype} v{ar.array_name} = {load_fn}(pg, {ptr});')

        # if 分支写操作
        if write_acc:
            dst_ptr = _format_array_ptr(write_acc.array_name, lv)
            src_name = (f'v{cond_arr_acc.array_name}' if cond_arr_acc else
                        f'v{orig.array_reads[0].array_name}' if orig.array_reads else 'svdup_f32(0)')
            if has_else:
                # svsel: pg=true选条件成立时的值，否则选0或else值
                dup_fn = _DUP.get(suf, 'svdup_f32')
                else_val = f'{dup_fn}(0)'
                # 若else分支是赋0，使用svsel(cond_pg, src, 0)
                inner.append(
                    f'{vtype} vresult = svsel_{suf.lstrip("_")}(cond_pg, {src_name}, {else_val});'
                )
                inner.append(f'{store_fn}(pg, {dst_ptr}, vresult);')
            else:
                # 无else：只在 cond_pg=true 的通道写
                inner.append(f'{store_fn}(cond_pg, {dst_ptr}, {src_name});')

        body.append(_ind('\n'.join(inner)))
        body.append('}')
        lines.append(_ind('\n'.join(body)))
        lines.append('}')

        return '\n'.join(lines)

    # ------------------------------------------------------------------ #
    #  MATRIX: 矩阵乘法，向量化最内层 j 循环                              #
    # ------------------------------------------------------------------ #

    def _gen_matrix(self, loop: AnalyzedLoop) -> str:
        orig = loop.original
        suf = loop.sve_type_suffix
        vtype = _VEC_TYPE.get(suf, 'svfloat32_t')
        load_fn = _LOAD.get(suf, 'svld1_f32')
        store_fn = _STORE.get(suf, 'svst1_f32')
        dup_fn = _DUP.get(suf, 'svdup_f32')
        mla_fn = _MLA.get(suf, 'svmla_f32_x')
        whilelt_fn = _WHILELT.get(loop.whilelt_suffix, 'svwhilelt_b32')
        vl_hint = loop.vl_hint

        # 从嵌套循环推断矩阵变量名
        # 典型矩阵：C[i*N+j] += A[i*K+k] * B[k*N+j]
        # 外层 i 循环 -> loop
        # 中层 k 循环 -> loop.inner_loops[0]
        # 内层 j 循环 -> k_loop.inner_loops[0]
        i_var = orig.loop_var
        i_end = orig.loop_end
        i_end_op = orig.loop_end_op

        # 推断矩阵维度变量名
        k_loop: Optional[LoopInfo] = orig.inner_loops[0] if orig.inner_loops else None
        j_loop: Optional[LoopInfo] = (k_loop.inner_loops[0]
                                       if k_loop and k_loop.inner_loops else None)

        if k_loop is None or j_loop is None:
            return self._gen_fallback(loop)

        k_var = k_loop.loop_var
        k_end = k_loop.loop_end
        j_var = j_loop.loop_var
        j_end = j_loop.loop_end

        j_end_expr = (f'(int64_t)({j_end}) + 1'
                      if j_loop.loop_end_op == '<=' else f'(int64_t)({j_end})')

        # 尝试从读写访问中识别 A、B、C
        # C[...] 是写目标，A/B 是读来源
        write_acc = _get_primary_write(j_loop)
        read_accs = _get_read_arrays(j_loop, exclude_name=write_acc.array_name if write_acc else None)

        c_name = write_acc.array_name if write_acc else 'C'
        # 找含有 k_var 下标的读数组（A 或 B）
        a_acc = next((r for r in read_accs if k_var in r.index_vars
                      and i_var in r.index_vars), None)
        b_acc = next((r for r in read_accs if k_var in r.index_vars
                      and j_var in r.index_vars), None)

        # fallback 名称
        a_name = a_acc.array_name if a_acc else ('A' if len(read_accs) > 0 else 'A')
        b_name = b_acc.array_name if b_acc else ('B' if len(read_accs) > 1 else 'B')

        # 推断 K、N 等维度（从读操作下标的常量因子推断，保守处理）
        # 直接使用循环变量端点作为维度变量名
        k_dim = k_end    # K
        j_dim = j_end    # N
        i_dim = i_end    # M

        lines: List[str] = []
        lines.append(f'/* SVE vectorized: MATRIX MUL {loop.sve_element_type} (j-loop vectorized) */\n{{')

        body = []
        body.append(f'int64_t sve_vl = {vl_hint};')
        body.append(f'svbool_t pg;')

        # i 循环（标量）
        i_end_cmp = f'{i_dim}' if i_end_op != '<=' else f'(int64_t)({i_dim}) + 1'
        body.append(f'for (int {i_var} = 0; {i_var} < {i_end_cmp}; {i_var}++) {{')

        i_inner = []
        # k 循环（标量）
        k_end_cmp = f'{k_dim}' if k_loop.loop_end_op != '<=' else f'(int64_t)({k_dim}) + 1'
        i_inner.append(f'for (int {k_var} = 0; {k_var} < {k_end_cmp}; {k_var}++) {{')

        k_inner = []
        # 广播 A[i*K+k] 为标量 → 向量
        # 从 a_acc 推断实际的索引表达式
        if a_acc and a_acc.pattern == a_acc.pattern.INDEXED_2D:
            # a[i][k] 形式
            k_inner.append(f'{vtype} va = {dup_fn}({a_name}[{i_var}][{k_var}]);')
        else:
            # a[i*K+k] 形式（平铺数组）
            k_inner.append(f'{vtype} va = {dup_fn}({a_name}[{i_var}*{k_dim}+{k_var}]);')

        # j 循环（向量化）
        k_inner.append(f'int64_t {j_var} = 0;')
        k_inner.append(f'for (; {j_var} < {j_end_expr}; {j_var} += sve_vl) {{')

        j_inner = []
        j_inner.append(f'pg = {whilelt_fn}({j_var}, {j_end_expr});')

        # 加载 B[k*N+j] / B[k][j]
        if b_acc and b_acc.pattern == b_acc.pattern.INDEXED_2D:
            j_inner.append(f'{vtype} vb = {load_fn}(pg, &{b_name}[{k_var}][{j_var}]);')
        else:
            j_inner.append(f'{vtype} vb = {load_fn}(pg, &{b_name}[{k_var}*{j_dim}+{j_var}]);')

        # 加载 C[i*N+j] / C[i][j]
        if write_acc and write_acc.pattern == write_acc.pattern.INDEXED_2D:
            j_inner.append(f'{vtype} vc = {load_fn}(pg, &{c_name}[{i_var}][{j_var}]);')
            j_inner.append(f'vc = {mla_fn}(pg, vc, va, vb);  /* vc += va * vb */')
            j_inner.append(f'{store_fn}(pg, &{c_name}[{i_var}][{j_var}], vc);')
        else:
            j_inner.append(f'{vtype} vc = {load_fn}(pg, &{c_name}[{i_var}*{j_dim}+{j_var}]);')
            j_inner.append(f'vc = {mla_fn}(pg, vc, va, vb);  /* vc += va * vb */')
            j_inner.append(f'{store_fn}(pg, &{c_name}[{i_var}*{j_dim}+{j_var}], vc);')

        k_inner.append(_ind('\n'.join(j_inner), 1))
        k_inner.append('}  /* j loop end */')

        i_inner.append(_ind('\n'.join(k_inner), 1))
        i_inner.append('}  /* k loop end */')

        body.append(_ind('\n'.join(i_inner), 1))
        body.append('}  /* i loop end */')

        lines.append(_ind('\n'.join(body)))
        lines.append('}')

        return '\n'.join(lines)

    # ------------------------------------------------------------------ #
    #  FALLBACK: 不可向量化，保留原始代码 + 注释                          #
    # ------------------------------------------------------------------ #

    def _gen_fallback(self, loop: AnalyzedLoop) -> str:
        orig = loop.original
        reason = loop.rejection_reason or f'模式 {loop.pattern.name} 不支持向量化'
        header = f'/* SVE-VECTORIZER: SKIPPED (line {orig.node_coord_start}) - {reason} */\n'
        original_code = ''.join(orig.raw_source_lines)
        return header + original_code

    def get_required_headers(self) -> List[str]:
        return REQUIRED_HEADERS


# ---------------------------------------------------------------------------
# 模块对外入口
# ---------------------------------------------------------------------------

def generate_sve_code(loop: AnalyzedLoop,
                      cfg: Optional[UnrollConfig] = None) -> str:
    """根据分析结果生成 SVE C 代码片段，cfg 由 LoopUnroller 提供"""
    return SVECodeGen().generate(loop, cfg)
