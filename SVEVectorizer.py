"""
SVEVectorizer.py
----------------
ARM SVE 自动向量化工具主入口。

用法:
    python SVEVectorizer.py input.c [-o output_sve.c] [--no-cpp] [--verbose] [--dry-run] [--report]
    python SVEVectorizer.py input.c --unroll 4 --prefetch-dist 16
    python SVEVectorizer.py input.c --save-stages   # 保存各阶段中间结果到 stages/ 目录

管道:
    C源文件 → CLoopExtraction → LoopAnalyzer → LoopUnroller → SVECodeGen → 输出 *_sve.c
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from CLoopExtraction import LoopInfo, extract_loops
from LoopAnalyzer import AnalyzedLoop, VectorizabilityStatus, analyze_loops
from LoopUnroller import UnrollConfig, unroll_loops
from SVECodeGen import REQUIRED_HEADERS, SVECodeGen, generate_sve_code


# ---------------------------------------------------------------------------
# 管道编排
# ---------------------------------------------------------------------------

class SVEVectorizer:

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._codegen = SVECodeGen()

    # ---- 主入口 ----

    def run(
        self,
        input_file: str,
        output_file: str,
        use_cpp: bool = True,
        dry_run: bool = False,
        print_report: bool = False,
        unroll_factor: int = 0,
        prefetch_dist: int = 0,
        save_stages: bool = False,
    ) -> bool:
        """
        完整向量化管道。返回 True 表示成功（即使有部分循环跳过）。
        save_stages=True 时，各阶段中间结果保存到 stages/ 子目录。
        """
        # 1. 读取源文件
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                source_lines = f.readlines()
        except OSError as e:
            print(f'[错误] 无法读取文件: {e}', file=sys.stderr)
            return False

        stem = Path(input_file).stem  # 用于阶段文件命名

        # 2. 提取循环
        if self.verbose:
            print(f'[信息] 正在解析: {input_file}')
        try:
            loops: List[LoopInfo] = extract_loops(input_file, use_cpp=use_cpp)
        except RuntimeError as e:
            print(f'[错误] 循环提取失败:\n  {e}', file=sys.stderr)
            return False

        if self.verbose:
            print(f'[信息] 解析得到 {len(loops)} 个 for 循环（含嵌套）')

        if save_stages:
            self._save_stage1(loops, stem)

        if not loops:
            print('[警告] 未发现任何 for 循环，输出与输入相同。')
            if not dry_run:
                self._write_with_headers(source_lines, output_file, headers_needed=False)
            return True

        # 3. 分析循环
        analyzed: List[AnalyzedLoop] = analyze_loops(loops)

        if self.verbose:
            print(f'[信息] 选择最内层循环进行向量化，共 {len(analyzed)} 个')

        if self.verbose:
            for al in analyzed:
                status_str = al.status.name
                print(f'  第 {al.original.node_coord_start} 行: '
                      f'[{al.pattern.name}] [{al.data_type.name}] → {status_str}')

        if save_stages:
            self._save_stage2(analyzed, stem)

        # 4. 循环展开决策（LoopUnroller）
        unroll_pairs = unroll_loops(
            analyzed,
            unroll_factor=unroll_factor,
            prefetch_dist=prefetch_dist,
        )
        if self.verbose:
            for al, cfg in unroll_pairs:
                print(f'     展开: unroll={cfg.unroll_factor} prefetch={cfg.enable_prefetch}'
                      f'  ({cfg.reason})')

        if save_stages:
            self._save_stage3(unroll_pairs, stem)

        # 5. 生成 SVE 代码（传入 UnrollConfig）
        snippets: List[str] = [
            self._codegen.generate(al, cfg) for al, cfg in unroll_pairs
        ]

        # 6. 嵌入代码
        modified_lines = self._embed_sve_code(source_lines, analyzed, snippets)

        # 7. 注入头文件
        modified_lines = self._inject_headers(modified_lines)

        # 8. 打印报告
        if print_report:
            print(self.generate_report(analyzed))

        # 9. 写出（非 dry-run）
        if not dry_run:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.writelines(modified_lines)
                print(f'[完成] 输出写入: {output_file}')
            except OSError as e:
                print(f'[错误] 写出文件失败: {e}', file=sys.stderr)
                return False

            if save_stages:
                self._save_stage4(output_file, stem)
        else:
            print('[dry-run] 仅分析，不写出文件。')

        return True

    # ---- 代码嵌入 ----

    def _embed_sve_code(
        self,
        source_lines: List[str],
        analyzed_loops: List[AnalyzedLoop],
        snippets: List[str],
    ) -> List[str]:
        """
        将 SVE 代码片段按行号替换原始循环代码。
        关键：按 node_coord_start 降序处理，避免行号偏移问题。
        analyzed_loops 为最内层循环集合。
        """
        # 构建替换任务列表，按起始行降序排列
        tasks: List[Tuple[int, int, str]] = []
        for al, snippet in zip(analyzed_loops, snippets):
            orig = al.original
            start = orig.node_coord_start - 1  # 转为 0-based 索引
            end = orig.node_coord_end           # 0-based exclusive（即 source_lines[start:end]）
            tasks.append((start, end, snippet))

        if not tasks:
            return list(source_lines)

        # 去重/防重叠：优先保留更内层（区间更短）的循环
        tasks.sort(key=lambda t: ((t[1] - t[0]), -t[0]))
        selected: List[Tuple[int, int, str]] = []
        for start, end, snippet in tasks:
            overlap = any(not (end <= s or start >= e) for s, e, _ in selected)
            if not overlap:
                selected.append((start, end, snippet))

        # 降序排列
        selected.sort(key=lambda t: t[0], reverse=True)

        result = list(source_lines)
        for start, end, snippet in selected:
            # 在替换位置添加原始行号注释 + SVE 代码
            orig_comment = f'/* [SVE-VECTORIZER] 原始循环位于第 {start + 1} 行 */\n'
            new_block = [orig_comment + snippet + '\n']
            result[start:end] = new_block

        return result

    # ---- 头文件注入 ----

    def _inject_headers(self, source_lines: List[str]) -> List[str]:
        """
        在文件顶部（首个 #include 之后，或文件头部）插入 SVE 所需头文件。
        若已存在则跳过。
        """
        result = list(source_lines)
        existing = ''.join(result)

        headers_to_add = [h for h in REQUIRED_HEADERS if h not in existing]
        if not headers_to_add:
            return result

        insert_lines = [f'#include {h}\n' for h in headers_to_add]

        # 找到首个 #include 的位置
        first_include_idx = next(
            (i for i, line in enumerate(result) if line.strip().startswith('#include')),
            None,
        )
        if first_include_idx is not None:
            # 在首个 #include 之前插入（保证位于所有头文件前面）
            insert_at = first_include_idx
        else:
            # 无 #include，插入文件头部
            insert_at = 0

        for line in reversed(insert_lines):
            result.insert(insert_at, line)

        return result

    def _write_with_headers(self, source_lines: List[str], output_file: str,
                             headers_needed: bool = True):
        lines = self._inject_headers(source_lines) if headers_needed else list(source_lines)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)

    # ---- 阶段文件保存 ----

    _STAGES_DIR = Path(__file__).parent / 'stages'

    def _stages_path(self, stem: str, suffix: str) -> Path:
        self._STAGES_DIR.mkdir(exist_ok=True)
        return self._STAGES_DIR / f'{stem}{suffix}'

    def _save_stage1(self, loops: List[LoopInfo], stem: str) -> None:
        """阶段1：循环提取结果（LoopInfo 列表）"""
        top_loops = [l for l in loops if l.nesting_level == 0]
        lines = [
            '=' * 64,
            '阶段 1：循环提取结果（CLoopExtraction）',
            f'共提取 {len(top_loops)} 个顶层循环',
            '=' * 64,
            '',
        ]
        for idx, lp in enumerate(top_loops, 1):
            end_op = lp.loop_end_op if lp.loop_end_op else '<'
            reads = ', '.join(
                f'{a.array_name}[{"+".join(a.index_vars) or str(a.index_offset)}]'
                for a in lp.array_reads
            ) or '（无）'
            writes = ', '.join(
                f'{a.array_name}[{"+".join(a.index_vars) or str(a.index_offset)}]'
                for a in lp.array_writes
            ) or '（无）'
            tc_str = '{' + ', '.join(f'{k}: {v}' for k, v in lp.type_context.items()) + '}'
            lines += [
                f'[循环 {idx}]  第 {lp.node_coord_start} 行  nesting_level={lp.nesting_level}',
                f'  loop_var    : {lp.loop_var}  [{lp.loop_start} .. {lp.loop_end}{end_op.replace("<",")")}  step={lp.loop_step}',
                f'  body_op     : {lp.body_operator.name}',
                f'  array_reads : {reads}',
                f'  array_writes: {writes}',
                f'  is_reduction: {lp.is_reduction}',
                f'  has_condition: {lp.has_condition}',
                f'  type_context: {tc_str}',
                f'  inner_loops : {len(lp.inner_loops)} 个',
                '',
            ]
        lines.append('=' * 64)
        path = self._stages_path(stem, '_stage1_loops.txt')
        path.write_text('\n'.join(lines), encoding='utf-8')
        print(f'[阶段1] 循环提取结果 → {path}')

    def _save_stage2(self, analyzed: List[AnalyzedLoop], stem: str) -> None:
        """阶段2：循环分析结果（AnalyzedLoop 列表）"""
        lines = [
            '=' * 64,
            '阶段 2：循环分析结果（LoopAnalyzer）',
            '=' * 64,
            '',
        ]
        for idx, al in enumerate(analyzed, 1):
            lines += [
                f'[循环 {idx}]  第 {al.original.node_coord_start} 行',
                f'  pattern     : {al.pattern.name}',
                f'  data_type   : {al.data_type.name}',
                f'  status      : {al.status.name}',
                f'  operator    : {al.operator.name}',
                f'  sve_suffix  : {al.sve_type_suffix}',
                f'  whilelt     : {al.whilelt_suffix}',
            ]
            if al.rejection_reason:
                lines.append(f'  rejection   : {al.rejection_reason}')
            lines.append('')
        lines.append('=' * 64)
        path = self._stages_path(stem, '_stage2_analyzed.txt')
        path.write_text('\n'.join(lines), encoding='utf-8')
        print(f'[阶段2] 循环分析结果 → {path}')

    def _save_stage3(
        self,
        unroll_pairs: List[Tuple[AnalyzedLoop, UnrollConfig]],
        stem: str,
    ) -> None:
        """阶段3：展开决策结果（UnrollConfig 列表）"""
        lines = [
            '=' * 64,
            '阶段 3：展开决策结果（LoopUnroller）',
            '=' * 64,
            '',
        ]
        for idx, (al, cfg) in enumerate(unroll_pairs, 1):
            lines += [
                f'[循环 {idx}]  第 {al.original.node_coord_start} 行'
                f'  {al.pattern.name} / {al.data_type.name}',
                f'  unroll_factor  : {cfg.unroll_factor}',
                f'  enable_prefetch: {cfg.enable_prefetch}',
                f'  prefetch_dist  : {cfg.prefetch_dist}',
                f'  reason         : {cfg.reason}',
                '',
            ]
        lines.append('=' * 64)
        path = self._stages_path(stem, '_stage3_unroll.txt')
        path.write_text('\n'.join(lines), encoding='utf-8')
        print(f'[阶段3] 展开决策结果 → {path}')

    def _save_stage4(self, output_file: str, stem: str) -> None:
        """阶段4：复制最终 SVE 代码到 stages/ 目录"""
        self._STAGES_DIR.mkdir(exist_ok=True)
        dest = self._stages_path(stem, '_stage4_sve.c')
        shutil.copy2(output_file, dest)
        print(f'[阶段4] 最终 SVE 代码   → {dest}')

    # ---- 分析报告 ----

    def generate_report(self, analyzed_loops: List[AnalyzedLoop]) -> str:
        total = len(analyzed_loops)
        vec_count = sum(1 for al in analyzed_loops
                        if al.status == VectorizabilityStatus.VECTORIZABLE)
        skip_count = total - vec_count

        lines = [
            '=' * 64,
            'SVE Vectorization Report',
            '=' * 64,
            f'发现循环总数:   {total}',
            f'可向量化:       {vec_count}',
            f'跳过:           {skip_count}',
            '',
        ]

        for al in analyzed_loops:
            orig = al.original
            status_str = al.status.name
            lines.append(
                f'第 {orig.node_coord_start} 行 '
                f'[{al.pattern.name}, {al.data_type.name}] → {status_str}'
            )
            if al.status == VectorizabilityStatus.VECTORIZABLE:
                # 列出使用的关键 intrinsics
                from SVECodeGen import (_ARITH_X, _LOAD, _STORE, _REDUCE,
                                        _WHILELT, _CMP, _MLA)
                suf = al.sve_type_suffix
                used = []
                load = _LOAD.get(suf)
                store = _STORE.get(suf)
                if load:
                    used.append(load)
                if store:
                    used.append(store)
                arith = _ARITH_X.get(al.operator, {}).get(suf)
                if arith:
                    used.append(arith)
                wl = _WHILELT.get(al.whilelt_suffix)
                if wl:
                    used.append(wl)
                if used:
                    lines.append(f'  intrinsics: {", ".join(used)}')
            elif al.rejection_reason:
                lines.append(f'  跳过原因: {al.rejection_reason}')
            lines.append('')

        lines.append('=' * 64)
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='SVEVectorizer',
        description='将 C 代码自动向量化为 ARM SVE intrinsics 代码',
    )
    parser.add_argument('input', help='输入 C 源文件路径')
    parser.add_argument(
        '-o', '--output', default=None,
        help='输出文件路径（默认: <input>_sve.c）',
    )
    parser.add_argument(
        '--no-cpp', action='store_true',
        help='禁用 C 预处理器（文件无系统头文件时使用）',
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='打印每个循环的分析过程',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='仅分析，不写出文件',
    )
    parser.add_argument(
        '--report', action='store_true',
        help='打印向量化分析报告',
    )
    parser.add_argument(
        '--unroll', type=int, default=0, metavar='N',
        help='强制覆盖展开因子（0=自动决策，默认）',
    )
    parser.add_argument(
        '--prefetch-dist', type=int, default=0, metavar='N',
        help='软件预取距离，单位为向量长度 vl（0=自动决策，默认）',
    )
    parser.add_argument(
        '--save-stages', action='store_true',
        help='保存各阶段中间结果到 stages/ 目录（stage1_loops / stage2_analyzed / stage3_unroll / stage4_sve）',
    )
    return parser


def main() -> int:
    parser = _build_cli()
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f'[错误] 文件不存在: {args.input}', file=sys.stderr)
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else input_path.with_name(input_path.stem + '_sve.c')
    )

    vectorizer = SVEVectorizer(verbose=args.verbose)
    success = vectorizer.run(
        input_file=str(input_path),
        output_file=str(output_path),
        use_cpp=not args.no_cpp,
        dry_run=args.dry_run,
        print_report=args.report,
        unroll_factor=args.unroll,
        prefetch_dist=args.prefetch_dist,
        save_stages=args.save_stages,
    )

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
