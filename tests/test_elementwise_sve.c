#include <arm_sve.h>
#include <stdint.h>
/* test_elementwise.c - 逐元素运算测试 */

/* Test 1: float 数组加法 */
void vec_add_float(float *a, float *b, float *c, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 5 行 */
/* SVE vectorized: ELEMENTWISE ADD float | unroll=2 prefetch=8 */
{
    int64_t vl  = svcntw();
    int64_t i = 0;
    svbool_t pg_all = svptrue_b32();  /* 全真谓词，主/清理循环复用 */
    /* 主循环：2×vl 元素/迭代，svptrue 无谓词计算开销 */
    for (; i + 2*vl <= (int64_t)(n); i += 2*vl) {
        svprfd(pg_all, &b[i + 8*vl], SV_PLDL1KEEP);
        svprfd(pg_all, &c[i + 8*vl], SV_PLDL1KEEP);
        svfloat32_t vb_0 = svld1_f32(pg_all, &b[i]);
        svfloat32_t vc_0 = svld1_f32(pg_all, &c[i]);
        svfloat32_t va_0 = svadd_f32_x(pg_all, vb_0, vc_0);
        svst1_f32(pg_all, &a[i], va_0);
        svfloat32_t vb_1 = svld1_f32(pg_all, &b[i + 1*vl]);
        svfloat32_t vc_1 = svld1_f32(pg_all, &c[i + 1*vl]);
        svfloat32_t va_1 = svadd_f32_x(pg_all, vb_1, vc_1);
        svst1_f32(pg_all, &a[i + 1*vl], va_1);
    }
    /* 清理循环：处理展开余量（最多 1 次），仍用 svptrue */
    for (; i + vl <= (int64_t)(n); i += vl) {
        svfloat32_t vb = svld1_f32(pg_all, &b[i]);
        svfloat32_t vc = svld1_f32(pg_all, &c[i]);
        svfloat32_t va = svadd_f32_x(pg_all, vb, vc);
        svst1_f32(pg_all, &a[i], va);
    }
    /* 尾部：svwhilelt 最多执行 1 次，处理不足一个向量的剩余元素 */
    if (i < (int64_t)(n)) {
        svbool_t pg = svwhilelt_b32(i, (int64_t)(n));
        svfloat32_t vb = svld1_f32(pg, &b[i]);
        svfloat32_t vc = svld1_f32(pg, &c[i]);
        svfloat32_t va = svadd_f32_x(pg, vb, vc);
        svst1_f32(pg, &a[i], va);
    }
}
}

/* Test 2: double 数组乘法 */
void vec_mul_double(double *a, double *b, double *c, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 12 行 */
/* SVE vectorized: ELEMENTWISE MUL double | unroll=2 prefetch=8 */
{
    int64_t vl  = svcntd();
    int64_t i = 0;
    svbool_t pg_all = svptrue_b64();  /* 全真谓词，主/清理循环复用 */
    /* 主循环：2×vl 元素/迭代，svptrue 无谓词计算开销 */
    for (; i + 2*vl <= (int64_t)(n); i += 2*vl) {
        svprfd(pg_all, &b[i + 8*vl], SV_PLDL1KEEP);
        svprfd(pg_all, &c[i + 8*vl], SV_PLDL1KEEP);
        svfloat64_t vb_0 = svld1_f64(pg_all, &b[i]);
        svfloat64_t vc_0 = svld1_f64(pg_all, &c[i]);
        svfloat64_t va_0 = svmul_f64_x(pg_all, vb_0, vc_0);
        svst1_f64(pg_all, &a[i], va_0);
        svfloat64_t vb_1 = svld1_f64(pg_all, &b[i + 1*vl]);
        svfloat64_t vc_1 = svld1_f64(pg_all, &c[i + 1*vl]);
        svfloat64_t va_1 = svmul_f64_x(pg_all, vb_1, vc_1);
        svst1_f64(pg_all, &a[i + 1*vl], va_1);
    }
    /* 清理循环：处理展开余量（最多 1 次），仍用 svptrue */
    for (; i + vl <= (int64_t)(n); i += vl) {
        svfloat64_t vb = svld1_f64(pg_all, &b[i]);
        svfloat64_t vc = svld1_f64(pg_all, &c[i]);
        svfloat64_t va = svmul_f64_x(pg_all, vb, vc);
        svst1_f64(pg_all, &a[i], va);
    }
    /* 尾部：svwhilelt 最多执行 1 次，处理不足一个向量的剩余元素 */
    if (i < (int64_t)(n)) {
        svbool_t pg = svwhilelt_b64(i, (int64_t)(n));
        svfloat64_t vb = svld1_f64(pg, &b[i]);
        svfloat64_t vc = svld1_f64(pg, &c[i]);
        svfloat64_t va = svmul_f64_x(pg, vb, vc);
        svst1_f64(pg, &a[i], va);
    }
}
}

/* Test 3: int 数组减法 */
void vec_sub_int(int *a, int *b, int *c, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 19 行 */
/* SVE vectorized: ELEMENTWISE SUB int32_t | unroll=2 prefetch=8 */
{
    int64_t vl  = svcntw();
    int64_t i = 0;
    svbool_t pg_all = svptrue_b32();  /* 全真谓词，主/清理循环复用 */
    /* 主循环：2×vl 元素/迭代，svptrue 无谓词计算开销 */
    for (; i + 2*vl <= (int64_t)(n); i += 2*vl) {
        svprfd(pg_all, &b[i + 8*vl], SV_PLDL1KEEP);
        svprfd(pg_all, &c[i + 8*vl], SV_PLDL1KEEP);
        svint32_t vb_0 = svld1_s32(pg_all, &b[i]);
        svint32_t vc_0 = svld1_s32(pg_all, &c[i]);
        svint32_t va_0 = svsub_s32_x(pg_all, vb_0, vc_0);
        svst1_s32(pg_all, &a[i], va_0);
        svint32_t vb_1 = svld1_s32(pg_all, &b[i + 1*vl]);
        svint32_t vc_1 = svld1_s32(pg_all, &c[i + 1*vl]);
        svint32_t va_1 = svsub_s32_x(pg_all, vb_1, vc_1);
        svst1_s32(pg_all, &a[i + 1*vl], va_1);
    }
    /* 清理循环：处理展开余量（最多 1 次），仍用 svptrue */
    for (; i + vl <= (int64_t)(n); i += vl) {
        svint32_t vb = svld1_s32(pg_all, &b[i]);
        svint32_t vc = svld1_s32(pg_all, &c[i]);
        svint32_t va = svsub_s32_x(pg_all, vb, vc);
        svst1_s32(pg_all, &a[i], va);
    }
    /* 尾部：svwhilelt 最多执行 1 次，处理不足一个向量的剩余元素 */
    if (i < (int64_t)(n)) {
        svbool_t pg = svwhilelt_b32(i, (int64_t)(n));
        svint32_t vb = svld1_s32(pg, &b[i]);
        svint32_t vc = svld1_s32(pg, &c[i]);
        svint32_t va = svsub_s32_x(pg, vb, vc);
        svst1_s32(pg, &a[i], va);
    }
}
}

/* Test 4: float 数组除法 */
void vec_div_float(float *a, float *b, float *c, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 26 行 */
/* SVE vectorized: ELEMENTWISE DIV float | unroll=2 prefetch=8 */
{
    int64_t vl  = svcntw();
    int64_t i = 0;
    svbool_t pg_all = svptrue_b32();  /* 全真谓词，主/清理循环复用 */
    /* 主循环：2×vl 元素/迭代，svptrue 无谓词计算开销 */
    for (; i + 2*vl <= (int64_t)(n); i += 2*vl) {
        svprfd(pg_all, &b[i + 8*vl], SV_PLDL1KEEP);
        svprfd(pg_all, &c[i + 8*vl], SV_PLDL1KEEP);
        svfloat32_t vb_0 = svld1_f32(pg_all, &b[i]);
        svfloat32_t vc_0 = svld1_f32(pg_all, &c[i]);
        svfloat32_t va_0 = svdiv_f32_x(pg_all, vb_0, vc_0);
        svst1_f32(pg_all, &a[i], va_0);
        svfloat32_t vb_1 = svld1_f32(pg_all, &b[i + 1*vl]);
        svfloat32_t vc_1 = svld1_f32(pg_all, &c[i + 1*vl]);
        svfloat32_t va_1 = svdiv_f32_x(pg_all, vb_1, vc_1);
        svst1_f32(pg_all, &a[i + 1*vl], va_1);
    }
    /* 清理循环：处理展开余量（最多 1 次），仍用 svptrue */
    for (; i + vl <= (int64_t)(n); i += vl) {
        svfloat32_t vb = svld1_f32(pg_all, &b[i]);
        svfloat32_t vc = svld1_f32(pg_all, &c[i]);
        svfloat32_t va = svdiv_f32_x(pg_all, vb, vc);
        svst1_f32(pg_all, &a[i], va);
    }
    /* 尾部：svwhilelt 最多执行 1 次，处理不足一个向量的剩余元素 */
    if (i < (int64_t)(n)) {
        svbool_t pg = svwhilelt_b32(i, (int64_t)(n));
        svfloat32_t vb = svld1_f32(pg, &b[i]);
        svfloat32_t vc = svld1_f32(pg, &c[i]);
        svfloat32_t va = svdiv_f32_x(pg, vb, vc);
        svst1_f32(pg, &a[i], va);
    }
}
}
