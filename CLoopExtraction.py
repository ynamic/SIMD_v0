"""
CLoopExtraction.py
------------------
使用 pycparser 对 C 源文件做 AST 解析，提取所有 for 循环的结构信息。
本模块是整个 ARM SVE 自动向量化工具链的数据源头。

对外唯一入口：
    extract_loops(c_file, use_cpp=True) -> List[LoopInfo]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

import re
import tempfile

import pycparser
from pycparser import c_ast, parse_file


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class OperatorKind(Enum):
    ADD     = auto()   # +  / +=
    SUB     = auto()   # -  / -=
    MUL     = auto()   # *  / *=
    DIV     = auto()   # /  / /=
    ASSIGN  = auto()   # =  (纯赋值，无运算)
    UNKNOWN = auto()


class AccessPattern(Enum):
    INDEXED_LINEAR = auto()   # a[i]
    INDEXED_2D     = auto()   # a[i][j]
    INDEXED_OFFSET = auto()   # a[i+k] / a[i-k]
    SCALAR         = auto()   # 普通标量变量


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ArrayAccess:
    """描述一次数组读/写访问"""
    array_name:   str               # 数组名，e.g. "a"
    index_vars:   List[str]         # 下标变量列表，e.g. ["i"] 或 ["i", "j"]
    index_offset: int               # 常量偏移，a[i+2] -> 2
    is_write:     bool              # True=左值(写)，False=右值(读)
    pattern:      AccessPattern
    c_type:       Optional[str] = None  # 元素类型，若能推断："float","double","int"


@dataclass
class ConditionInfo:
    """描述循环体内的条件分支"""
    condition_var:    str        # 被判断的数组/变量，e.g. "a"
    condition_op:     str        # 比较符，e.g. ">", "<=", "=="
    condition_val:    str        # 右侧值，e.g. "0", "0.0f", 或变量名
    true_branch_ops:  List[str]  # if 成立分支描述
    false_branch_ops: List[str]  # else 分支描述（可为空）


@dataclass
class LoopInfo:
    """从 AST 提取的单个 for 循环的完整描述"""

    # --- 循环控制 ---
    loop_var:    str   # 循环变量，e.g. "i"
    loop_start:  str   # 初始值，e.g. "0"
    loop_end:    str   # 终止值变量/表达式，e.g. "n"
    loop_step:   int   # 步长（若非常量则为 0 表示未知）
    loop_end_op: str   # 终止判断符："<" / "<=" / "!=" / ">"

    # --- 源位置 ---
    source_file:       str
    node_coord_start:  int        # 循环 for 关键字所在行（1-based）
    node_coord_end:    int        # 循环最后一行（估算）
    raw_source_lines:  List[str]  # 原始 C 行列表（用于回写）

    # --- 数组访问 ---
    array_reads:    List[ArrayAccess]
    array_writes:   List[ArrayAccess]
    scalars_read:   List[str]
    scalars_written: List[str]

    # --- 操作 ---
    body_operator:  OperatorKind
    is_reduction:   bool
    reduction_var:  Optional[str]         # 归约变量名
    reduction_op:   Optional[OperatorKind]

    # --- 条件 ---
    has_condition:  bool
    condition_info: Optional[ConditionInfo]

    # --- 嵌套 ---
    nesting_level:    int              # 0 = 最外层
    parent_loop_var:  Optional[str]   # 父循环变量名
    inner_loops:      List[LoopInfo]  # 直接内层循环

    # --- 类型上下文（声明推断）---
    type_context: Dict[str, str]  # {"a": "float*", "n": "int"}


# ---------------------------------------------------------------------------
# AST 辅助工具
# ---------------------------------------------------------------------------

def _strip_c_comments(source: str) -> str:
    """
    移除 C 风格注释（/* ... */ 和 // ...），
    用于 use_cpp=False 时让 pycparser 能正常解析。
    """
    # 同时处理块注释和行注释；替换时保留换行数量，避免坐标行号漂移
    pattern = re.compile(r'//[^\n]*|/\*.*?\*/', re.DOTALL)

    def _replacer(match: re.Match) -> str:
        text = match.group(0)
        return ''.join('\n' if ch == '\n' else ' ' for ch in text)

    return pattern.sub(_replacer, source)


def _get_fake_libc_path() -> str:
    """返回 pycparser 附带的 fake_libc_include 目录路径"""
    pkg_dir = os.path.dirname(pycparser.__file__)
    candidate = os.path.join(pkg_dir, 'utils', 'fake_libc_include')
    if os.path.isdir(candidate):
        return candidate
    candidate2 = os.path.join(pkg_dir, 'fake_libc_include')
    if os.path.isdir(candidate2):
        return candidate2
    return candidate


def _estimate_end_line(node) -> int:
    """递归遍历 AST 节点，找到子树中最大行号（pycparser 不直接提供结束行）"""
    max_line = 0
    if hasattr(node, 'coord') and node.coord:
        max_line = node.coord.line
    for _, child in node.children():
        child_max = _estimate_end_line(child)
        if child_max > max_line:
            max_line = child_max
    return max_line


def _op_str_to_kind(op: str) -> OperatorKind:
    return {
        '+': OperatorKind.ADD, '+=': OperatorKind.ADD,
        '-': OperatorKind.SUB, '-=': OperatorKind.SUB,
        '*': OperatorKind.MUL, '*=': OperatorKind.MUL,
        '/': OperatorKind.DIV, '/=': OperatorKind.DIV,
        '=': OperatorKind.ASSIGN,
    }.get(op, OperatorKind.UNKNOWN)


def _node_to_str(node) -> str:
    """将简单 AST 节点转为字符串表示"""
    if node is None:
        return ''
    if isinstance(node, c_ast.Constant):
        return node.value
    if isinstance(node, c_ast.ID):
        return node.name
    if isinstance(node, c_ast.BinaryOp):
        return f'({_node_to_str(node.left)} {node.op} {_node_to_str(node.right)})'
    if isinstance(node, c_ast.UnaryOp):
        if node.op in ('p++', 'p--'):
            return _node_to_str(node.expr)
        return f'{node.op}{_node_to_str(node.expr)}'
    if isinstance(node, c_ast.ArrayRef):
        return f'{_node_to_str(node.name)}[{_node_to_str(node.subscript)}]'
    if isinstance(node, c_ast.Cast):
        return _node_to_str(node.expr)
    if isinstance(node, c_ast.NamedInitializer):
        return _node_to_str(node.expr)
    return '?'


# ---------------------------------------------------------------------------
# 循环体分析辅助
# ---------------------------------------------------------------------------

class _BodyAnalyzer:
    """
    分析单个 for 循环体，提取数组读写、标量、操作类型、归约、条件分支信息。
    """

    def __init__(self, loop_var: str, type_context: Dict[str, str]):
        self.loop_var = loop_var
        self.type_context = type_context
        self.array_reads:     List[ArrayAccess] = []
        self.array_writes:    List[ArrayAccess] = []
        self.scalars_read:    List[str] = []
        self.scalars_written: List[str] = []
        self.body_operator:   OperatorKind = OperatorKind.UNKNOWN
        self.is_reduction:    bool = False
        self.reduction_var:   Optional[str] = None
        self.reduction_op:    Optional[OperatorKind] = None
        self.has_condition:   bool = False
        self.condition_info:  Optional[ConditionInfo] = None

    def analyze(self, stmt):
        self._visit_stmt(stmt)

    # ---- 语句访问 ----

    def _visit_stmt(self, node):
        if node is None:
            return
        if isinstance(node, c_ast.Compound):
            for _, child in node.children():
                self._visit_stmt(child)
        elif isinstance(node, c_ast.Assignment):
            self._visit_assignment(node)
        elif isinstance(node, c_ast.If):
            self._visit_if(node)
        elif isinstance(node, c_ast.For):
            pass  # 内层 for 由外层 LoopExtractor 单独处理
        elif isinstance(node, c_ast.Decl):
            if node.init:
                self._collect_reads(node.init)
        else:
            for _, child in node.children():
                self._visit_stmt(child)

    def _visit_assignment(self, node: c_ast.Assignment):
        lhs = node.lvalue
        rhs = node.rvalue
        op_str = node.op

        # 左值处理
        if isinstance(lhs, c_ast.ArrayRef):
            acc = self._parse_array_ref(lhs, is_write=True)
            self.array_writes.append(acc)
            if self.body_operator == OperatorKind.UNKNOWN:
                self.body_operator = _op_str_to_kind(op_str)
            # 复合赋值左值也是一次读
            if op_str != '=':
                self.array_reads.append(self._parse_array_ref(lhs, is_write=False))
        elif isinstance(lhs, c_ast.ID):
            var_name = lhs.name
            if var_name not in self.scalars_written:
                self.scalars_written.append(var_name)
            if self.body_operator == OperatorKind.UNKNOWN:
                self.body_operator = _op_str_to_kind(op_str)
            if op_str in ('+=', '-=', '*=', '/='):
                self._check_reduction(var_name, rhs, op_str)

        # 对纯赋值（=），尝试从 RHS BinaryOp 提取实际操作符
        # e.g. a[i] = b[i] + c[i]  →  operator 应为 ADD
        if op_str == '=' and isinstance(rhs, c_ast.BinaryOp):
            inferred = _op_str_to_kind(rhs.op)
            if inferred != OperatorKind.UNKNOWN:
                self.body_operator = inferred

        # 右值递归收集读
        self._collect_reads(rhs)

    def _visit_if(self, node: c_ast.If):
        self.has_condition = True
        cond = node.cond
        cond_var, cond_op, cond_val = '', '', ''

        if isinstance(cond, c_ast.BinaryOp):
            cond_op = cond.op
            if isinstance(cond.left, c_ast.ArrayRef):
                inner = cond.left
                while isinstance(inner, c_ast.ArrayRef):
                    inner = inner.name
                cond_var = inner.name if isinstance(inner, c_ast.ID) else '?'
                self.array_reads.append(self._parse_array_ref(cond.left, is_write=False))
            elif isinstance(cond.left, c_ast.ID):
                cond_var = cond.left.name
            cond_val = _node_to_str(cond.right)

        true_ops: List[str] = []
        false_ops: List[str] = []

        if node.iftrue:
            self._visit_stmt(node.iftrue)
            true_ops = self._describe_stmt(node.iftrue)
        if node.iffalse:
            self._visit_stmt(node.iffalse)
            false_ops = self._describe_stmt(node.iffalse)

        self.condition_info = ConditionInfo(
            condition_var=cond_var,
            condition_op=cond_op,
            condition_val=cond_val,
            true_branch_ops=true_ops,
            false_branch_ops=false_ops,
        )

    # ---- 数组访问解析 ----

    def _parse_array_ref(self, node: c_ast.ArrayRef, is_write: bool) -> ArrayAccess:
        """
        解析 ArrayRef 节点，支持 a[i]、a[i][j]、a[i+k]
        """
        # 展开嵌套 ArrayRef 以获取数组名和所有下标
        subscripts = []
        tmp = node
        while isinstance(tmp, c_ast.ArrayRef):
            subscripts.insert(0, tmp.subscript)
            tmp = tmp.name

        if isinstance(tmp, c_ast.ID):
            array_name = tmp.name
        else:
            array_name = _node_to_str(tmp)

        index_vars: List[str] = []
        total_offset = 0
        for sub in subscripts:
            vars_, off = self._parse_subscript(sub)
            index_vars.extend(vars_)
            total_offset += off

        if len(subscripts) > 1:
            pattern = AccessPattern.INDEXED_2D
        elif total_offset != 0:
            pattern = AccessPattern.INDEXED_OFFSET
        else:
            pattern = AccessPattern.INDEXED_LINEAR

        raw_type = self.type_context.get(array_name, '')
        c_type = raw_type.rstrip('*').strip() if raw_type else None

        return ArrayAccess(
            array_name=array_name,
            index_vars=index_vars,
            index_offset=total_offset,
            is_write=is_write,
            pattern=pattern,
            c_type=c_type,
        )

    def _parse_subscript(self, node) -> Tuple[List[str], int]:
        """
        解析下标表达式，返回 (变量名列表, 常量偏移)。
        支持 +、-、* 以及常量。
        乘法（如 i*N）中的变量名也会被收集（用于矩阵访问模式识别）。
        """
        if isinstance(node, c_ast.ID):
            return ([node.name], 0)
        if isinstance(node, c_ast.Constant):
            try:
                return ([], int(node.value))
            except ValueError:
                return ([], 0)
        if isinstance(node, c_ast.BinaryOp):
            lv, lo = self._parse_subscript(node.left)
            rv, ro = self._parse_subscript(node.right)
            if node.op == '+':
                return (lv + rv, lo + ro)
            if node.op == '-':
                if not rv:
                    return (lv, lo - ro)
                return (lv, lo)
            if node.op == '*':
                # 乘法：收集所有变量名，偏移不追踪
                return (lv + rv, 0)
        if isinstance(node, c_ast.Cast):
            return self._parse_subscript(node.expr)
        return ([], 0)

    def _collect_reads(self, node):
        """递归收集表达式中所有数组读和标量读"""
        if node is None:
            return
        if isinstance(node, c_ast.ArrayRef):
            acc = self._parse_array_ref(node, is_write=False)
            sig = (acc.array_name, tuple(acc.index_vars), acc.index_offset, acc.pattern)
            already = any(
                (r.array_name, tuple(r.index_vars), r.index_offset, r.pattern) == sig
                for r in self.array_reads
            )
            if not already:
                self.array_reads.append(acc)
            # 只递归下标，不再递归 name（避免 a[i][j] -> a[i] 重复收集）
            self._collect_reads(node.subscript)
        elif isinstance(node, c_ast.ID):
            if (node.name not in self.scalars_read and
                    node.name not in self.scalars_written):
                self.scalars_read.append(node.name)
        else:
            for _, child in node.children():
                self._collect_reads(child)

    def _check_reduction(self, scalar_var: str, rhs, compound_op: str):
        """检测 scalar += array[i] 归约模式"""
        if self._has_array_ref(rhs):
            self.is_reduction = True
            self.reduction_var = scalar_var
            self.reduction_op = _op_str_to_kind(compound_op)

    def _has_array_ref(self, node) -> bool:
        if isinstance(node, c_ast.ArrayRef):
            return True
        for _, child in node.children():
            if self._has_array_ref(child):
                return True
        return False

    def _describe_stmt(self, node) -> List[str]:
        """返回语句的简单文字描述（用于 ConditionInfo）"""
        result = []
        if isinstance(node, c_ast.Assignment):
            result.append(f'{_node_to_str(node.lvalue)} {node.op} {_node_to_str(node.rvalue)}')
        elif isinstance(node, c_ast.Compound):
            for _, child in node.children():
                result.extend(self._describe_stmt(child))
        return result


# ---------------------------------------------------------------------------
# AST 遍历器
# ---------------------------------------------------------------------------

class LoopExtractor(c_ast.NodeVisitor):
    """
    遍历整个 AST，提取所有 for 循环并生成 LoopInfo 列表。
    """

    def __init__(self, source_lines: List[str], filename: str):
        self._source_lines = source_lines
        self._filename = filename
        self._type_context: Dict[str, str] = {}
        self._nesting_level: int = 0
        self._parent_loop_var: Optional[str] = None
        self.all_loops: List[LoopInfo] = []

    def visit_Decl(self, node: c_ast.Decl):
        """收集变量/参数声明以建立类型上下文"""
        if node.name and node.type:
            self._type_context[node.name] = self._type_node_to_str(node.type)
        self.generic_visit(node)

    def _type_node_to_str(self, type_node) -> str:
        if isinstance(type_node, (c_ast.ArrayDecl, c_ast.PtrDecl)):
            return self._type_node_to_str(type_node.type) + '*'
        if isinstance(type_node, c_ast.TypeDecl):
            if isinstance(type_node.type, c_ast.IdentifierType):
                return ' '.join(type_node.type.names)
        if isinstance(type_node, c_ast.IdentifierType):
            return ' '.join(type_node.names)
        return 'unknown'

    def visit_For(self, node: c_ast.For):
        loop_var, loop_start = self._parse_init(node.init)
        loop_end, loop_end_op = self._parse_cond(node.cond, loop_var)
        loop_step = self._parse_next(node.next, loop_var)

        start_line = node.coord.line if node.coord else 0
        end_line = _estimate_end_line(node)
        raw_lines = self._source_lines[start_line - 1: end_line] if start_line > 0 else []

        cur_nesting = self._nesting_level
        cur_parent = self._parent_loop_var

        analyzer = _BodyAnalyzer(loop_var, dict(self._type_context))
        analyzer.analyze(node.stmt)

        info = LoopInfo(
            loop_var=loop_var,
            loop_start=loop_start,
            loop_end=loop_end,
            loop_step=loop_step,
            loop_end_op=loop_end_op,
            source_file=self._filename,
            node_coord_start=start_line,
            node_coord_end=end_line,
            raw_source_lines=raw_lines,
            array_reads=analyzer.array_reads,
            array_writes=analyzer.array_writes,
            scalars_read=analyzer.scalars_read,
            scalars_written=analyzer.scalars_written,
            body_operator=analyzer.body_operator,
            is_reduction=analyzer.is_reduction,
            reduction_var=analyzer.reduction_var,
            reduction_op=analyzer.reduction_op,
            has_condition=analyzer.has_condition,
            condition_info=analyzer.condition_info,
            nesting_level=cur_nesting,
            parent_loop_var=cur_parent,
            inner_loops=[],
            type_context=dict(self._type_context),
        )
        self.all_loops.append(info)

        # 递归处理内层循环
        self._nesting_level += 1
        self._parent_loop_var = loop_var
        self.generic_visit(node)
        self._nesting_level = cur_nesting
        self._parent_loop_var = cur_parent

    def _parse_init(self, init_node) -> Tuple[str, str]:
        if init_node is None:
            return ('i', '0')
        if isinstance(init_node, c_ast.Decl):
            var = init_node.name or 'i'
            start = _node_to_str(init_node.init) if init_node.init else '0'
            return (var, start)
        if isinstance(init_node, c_ast.DeclList):
            if init_node.decls:
                return self._parse_init(init_node.decls[0])
        if isinstance(init_node, c_ast.Assignment):
            return (_node_to_str(init_node.lvalue), _node_to_str(init_node.rvalue))
        return ('i', '0')

    def _parse_cond(self, cond_node, loop_var: str) -> Tuple[str, str]:
        if cond_node is None:
            return ('n', '<')
        if isinstance(cond_node, c_ast.BinaryOp):
            op = cond_node.op
            left = _node_to_str(cond_node.left)
            right = _node_to_str(cond_node.right)
            if left == loop_var:
                return (right, op)
            reverse_map = {'>': '<', '>=': '<=', '<': '>', '<=': '>='}
            if right == loop_var and op in reverse_map:
                return (left, reverse_map[op])
            return (right, op)
        return ('n', '<')

    def _parse_next(self, next_node, loop_var: str) -> int:
        if next_node is None:
            return 0
        if isinstance(next_node, c_ast.UnaryOp):
            if next_node.op in ('p++', '++'):
                return 1
            if next_node.op in ('p--', '--'):
                return -1
        if isinstance(next_node, c_ast.Assignment):
            if next_node.op == '+=':
                try:
                    return int(_node_to_str(next_node.rvalue))
                except ValueError:
                    return 0
            if next_node.op == '-=':
                try:
                    return -int(_node_to_str(next_node.rvalue))
                except ValueError:
                    return 0
            if next_node.op == '=':
                rhs = next_node.rvalue
                if isinstance(rhs, c_ast.BinaryOp) and rhs.op == '+':
                    for side in (rhs.left, rhs.right):
                        if _node_to_str(side) == loop_var:
                            other = rhs.right if side is rhs.left else rhs.left
                            try:
                                return int(_node_to_str(other))
                            except ValueError:
                                pass
        return 0


# ---------------------------------------------------------------------------
# 后处理：建立父子嵌套关系
# ---------------------------------------------------------------------------

def _build_nesting_tree(all_loops: List[LoopInfo]) -> List[LoopInfo]:
    """将扁平循环列表按行号范围建立父子关系，返回顶层循环列表"""
    top_level = [l for l in all_loops if l.nesting_level == 0]

    def _attach(parent: LoopInfo, pool: List[LoopInfo]):
        for loop in pool:
            if (loop.nesting_level == parent.nesting_level + 1 and
                    loop.node_coord_start >= parent.node_coord_start and
                    loop.node_coord_end <= parent.node_coord_end):
                parent.inner_loops.append(loop)
                deeper = [l for l in pool if l.nesting_level > loop.nesting_level]
                _attach(loop, deeper)

    inner_pool = [l for l in all_loops if l.nesting_level > 0]
    for top in top_level:
        _attach(top, inner_pool)

    return top_level


# ---------------------------------------------------------------------------
# 模块对外入口
# ---------------------------------------------------------------------------

def extract_loops(c_file: str, use_cpp: bool = True) -> List[LoopInfo]:
    """
    解析 C 源文件，提取所有 for 循环信息。

    参数:
        c_file   : C 源文件路径
        use_cpp  : 是否调用 GCC 预处理（处理 #include/#define）
                   文件不含系统头文件时可设为 False

    返回:
        List[LoopInfo]  顶层循环列表，inner_loops 含嵌套子循环
    """
    with open(c_file, 'r', encoding='utf-8') as f:
        source_lines = f.readlines()

    if use_cpp:
        fake_libc = _get_fake_libc_path()
        try:
            ast = parse_file(
                c_file,
                use_cpp=True,
                cpp_path='gcc',
                cpp_args=[
                    '-E',
                    r'-I' + fake_libc,
                    r'-D__attribute__(x)=',
                    r'-D__extension__=',
                    r'-D__restrict=',
                    r'-D__inline=inline',
                ],
            )
        except Exception as e:
            raise RuntimeError(
                f"pycparser 解析失败: {e}\n"
                "  提示：若文件无系统头文件，可使用 use_cpp=False"
            ) from e
    else:
        # pycparser 不支持注释，先剥离后写入临时文件再解析
        stripped = _strip_c_comments(''.join(source_lines))
        tmp_path = ''
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.c', delete=False, encoding='utf-8'
            ) as tmp:
                tmp.write(stripped)
                tmp_path = tmp.name
            ast = parse_file(tmp_path, use_cpp=False)
        except Exception as e:
            raise RuntimeError(f"pycparser 解析失败: {e}") from e
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    extractor = LoopExtractor(source_lines, c_file)
    extractor.visit(ast)

    return _build_nesting_tree(extractor.all_loops)