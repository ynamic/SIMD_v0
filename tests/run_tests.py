"""
run_tests.py
------------
ARM SVE 自动向量化工具的自动化测试。
验证各模式输出是否包含正确的 SVE intrinsics 关键字。

运行方式：
    cd /home/pineipple/code/Py_Project/SIMD
    python tests/run_tests.py
"""

import subprocess
import sys
import unittest
from pathlib import Path

# 项目根目录（tests 的父目录）
ROOT = Path(__file__).parent.parent
VECTORIZER = ROOT / 'SVEVectorizer.py'
TESTS_DIR = Path(__file__).parent


def _vectorize(c_file: str, extra_args: list = None) -> str:
    """运行向量化器，返回输出文件内容"""
    out_file = '/tmp/_sve_test_output.c'
    cmd = [
        sys.executable, str(VECTORIZER),
        c_file,
        '-o', out_file,
        '--no-cpp',   # 测试文件无系统头文件，无需 cpp
    ]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'向量化器运行失败:\n  stdout: {result.stdout}\n  stderr: {result.stderr}'
        )
    return Path(out_file).read_text(encoding='utf-8')


class TestElementwise(unittest.TestCase):
    """逐元素运算向量化测试"""

    @classmethod
    def setUpClass(cls):
        cls.output = _vectorize(str(TESTS_DIR / 'test_elementwise.c'))

    def test_has_arm_sve_header(self):
        self.assertIn('<arm_sve.h>', self.output, '缺少 <arm_sve.h> 头文件')

    def test_has_stdint_header(self):
        self.assertIn('<stdint.h>', self.output, '缺少 <stdint.h> 头文件')

    def test_has_svld1_f32(self):
        self.assertIn('svld1_f32', self.output, '缺少 svld1_f32（float加载）')

    def test_has_svst1_f32(self):
        self.assertIn('svst1_f32', self.output, '缺少 svst1_f32（float存储）')

    def test_has_svadd_f32(self):
        self.assertIn('svadd_f32_x', self.output, '缺少 svadd_f32_x（float加法）')

    def test_has_svmul_f64(self):
        self.assertIn('svmul_f64_x', self.output, '缺少 svmul_f64_x（double乘法）')

    def test_has_svsub_s32(self):
        self.assertIn('svsub_s32_x', self.output, '缺少 svsub_s32_x（int减法）')

    def test_has_whilelt(self):
        self.assertTrue(
            'svwhilelt_b32' in self.output or 'svwhilelt_b64' in self.output,
            '缺少 svwhilelt（循环谓词生成）'
        )

    def test_has_sve_vectorized_comment(self):
        self.assertIn('SVE vectorized: ELEMENTWISE', self.output, '缺少向量化注释')


class TestReduction(unittest.TestCase):
    """归约运算向量化测试"""

    @classmethod
    def setUpClass(cls):
        cls.output = _vectorize(str(TESTS_DIR / 'test_reduction.c'))

    def test_has_svaddv_f32(self):
        self.assertIn('svaddv_f32', self.output, '缺少 svaddv_f32（float水平归约）')

    def test_has_svdup_f32(self):
        self.assertIn('svdup_f32', self.output, '缺少 svdup_f32（向量累加器初始化）')

    def test_has_svadd_f32_m(self):
        self.assertIn('svadd_f32_m', self.output, '缺少 svadd_f32_m（merge形式归约累加）')

    def test_has_reduction_comment(self):
        self.assertIn('SVE vectorized: REDUCTION', self.output, '缺少归约向量化注释')

    def test_has_svaddv_s32(self):
        self.assertIn('svaddv_s32', self.output, '缺少 svaddv_s32（int归约）')

    def test_has_svaddv_f64(self):
        self.assertIn('svaddv_f64', self.output, '缺少 svaddv_f64（double归约）')


class TestConditional(unittest.TestCase):
    """条件向量化测试"""

    @classmethod
    def setUpClass(cls):
        cls.output = _vectorize(str(TESTS_DIR / 'test_conditional.c'))

    def test_has_svcmpgt_f32(self):
        self.assertIn('svcmpgt_f32', self.output, '缺少 svcmpgt_f32（浮点大于比较）')

    def test_has_svst1_f32(self):
        self.assertIn('svst1_f32', self.output, '缺少 svst1_f32（条件存储）')

    def test_has_conditional_comment(self):
        self.assertIn('SVE vectorized: CONDITIONAL', self.output, '缺少条件向量化注释')

    def test_has_cond_pg(self):
        self.assertIn('cond_pg', self.output, '缺少条件谓词变量 cond_pg')

    def test_has_svcmplt_f32(self):
        # clamp_negative 使用 < 比较
        self.assertIn('svcmplt_f32', self.output, '缺少 svcmplt_f32（小于比较）')


class TestMatrix(unittest.TestCase):
    """矩阵运算向量化测试"""

    @classmethod
    def setUpClass(cls):
        cls.output = _vectorize(str(TESTS_DIR / 'test_matrix.c'))

    def test_has_svmla_f32(self):
        self.assertIn('svmla_f32_x', self.output, '缺少 svmla_f32_x（FMA乘加）')

    def test_has_svdup_f32(self):
        self.assertIn('svdup_f32', self.output, '缺少 svdup_f32（标量广播）')

    def test_has_svld1_f32(self):
        self.assertIn('svld1_f32', self.output, '缺少 svld1_f32（矩阵元素加载）')

    def test_has_svst1_f32(self):
        self.assertIn('svst1_f32', self.output, '缺少 svst1_f32（矩阵元素存储）')

    def test_has_matrix_comment(self):
        self.assertIn('SVE vectorized: MATRIX', self.output, '缺少矩阵向量化注释')

    def test_has_j_loop_vectorized(self):
        self.assertIn('j-loop vectorized', self.output, '缺少 j 循环向量化说明')


class TestHeaders(unittest.TestCase):
    """所有输出文件都应包含必要头文件"""

    def _check_headers(self, c_file: str):
        output = _vectorize(c_file)
        self.assertIn('<arm_sve.h>', output, f'{c_file}: 缺少 <arm_sve.h>')
        self.assertIn('<stdint.h>', output, f'{c_file}: 缺少 <stdint.h>')

    def test_elementwise_headers(self):
        self._check_headers(str(TESTS_DIR / 'test_elementwise.c'))

    def test_reduction_headers(self):
        self._check_headers(str(TESTS_DIR / 'test_reduction.c'))

    def test_conditional_headers(self):
        self._check_headers(str(TESTS_DIR / 'test_conditional.c'))

    def test_matrix_headers(self):
        self._check_headers(str(TESTS_DIR / 'test_matrix.c'))


class TestReportMode(unittest.TestCase):
    """--report 和 --dry-run 模式测试"""

    def test_dry_run_no_output(self):
        """--dry-run 不应写出文件"""
        import os
        out_file = '/tmp/_dry_run_test_output.c'
        if os.path.exists(out_file):
            os.remove(out_file)
        subprocess.run(
            [sys.executable, str(VECTORIZER),
             str(TESTS_DIR / 'test_elementwise.c'),
             '-o', out_file, '--no-cpp', '--dry-run'],
            cwd=str(ROOT), capture_output=True,
        )
        self.assertFalse(os.path.exists(out_file), '--dry-run 不应写出文件')

    def test_report_output(self):
        """--report 应输出包含统计信息的报告"""
        result = subprocess.run(
            [sys.executable, str(VECTORIZER),
             str(TESTS_DIR / 'test_elementwise.c'),
             '--no-cpp', '--dry-run', '--report'],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        self.assertIn('SVE Vectorization Report', combined, '报告未输出')
        self.assertIn('发现循环总数', combined, '报告缺少统计信息')


if __name__ == '__main__':
    print(f'测试目录: {TESTS_DIR}')
    print(f'向量化器: {VECTORIZER}')
    print()
    unittest.main(verbosity=2)
