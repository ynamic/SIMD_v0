#include <arm_sve.h>
#include <stdint.h>
/* test_conditional.c - 带条件循环测试 */

/* Test 1: ReLU (if/else) */
void relu(float *a, float *b, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 5 行 */
/* SVE vectorized: CONDITIONAL (a[i]>0.0f) float */
{
    int64_t i = 0;
    int64_t sve_vl = svcntw();
    svbool_t pg, cond_pg;
    for (; i < (int64_t)(n); i += sve_vl) {
        pg = svwhilelt_b32(i, (int64_t)(n));
        svfloat32_t va = svld1_f32(pg, &a[i]);
        cond_pg = svcmpgt_f32(pg, va, svdup_f32(0.0f));  /* if(a[i]>0.0f) */
        svfloat32_t vresult = svsel_f32(cond_pg, va, svdup_f32(0));
        svst1_f32(pg, &b[i], vresult);
    }
}
}

/* Test 2: 单分支阈值（无 else） */
void threshold(float *a, float *b, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 16 行 */
/* SVE vectorized: CONDITIONAL (a[i]>0.5f) float */
{
    int64_t i = 0;
    int64_t sve_vl = svcntw();
    svbool_t pg, cond_pg;
    for (; i < (int64_t)(n); i += sve_vl) {
        pg = svwhilelt_b32(i, (int64_t)(n));
        svfloat32_t va = svld1_f32(pg, &a[i]);
        cond_pg = svcmpgt_f32(pg, va, svdup_f32(0.5f));  /* if(a[i]>0.5f) */
        svst1_f32(cond_pg, &b[i], va);
    }
}
}

/* Test 3: 负值清零 */
void clamp_negative(float *a, int n) {
/* [SVE-VECTORIZER] 原始循环位于第 25 行 */
/* SVE vectorized: CONDITIONAL (a[i]<0.0f) float */
{
    int64_t i = 0;
    int64_t sve_vl = svcntw();
    svbool_t pg, cond_pg;
    for (; i < (int64_t)(n); i += sve_vl) {
        pg = svwhilelt_b32(i, (int64_t)(n));
        svfloat32_t va = svld1_f32(pg, &a[i]);
        cond_pg = svcmplt_f32(pg, va, svdup_f32(0.0f));  /* if(a[i]<0.0f) */
        svst1_f32(cond_pg, &a[i], va);
    }
}
}
