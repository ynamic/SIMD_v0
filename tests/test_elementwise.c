/* test_elementwise.c - 逐元素运算测试 */

/* Test 1: float 数组加法 */
void vec_add_float(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) {
        a[i] = b[i] + c[i];
    }
}

/* Test 2: double 数组乘法 */
void vec_mul_double(double *a, double *b, double *c, int n) {
    for (int i = 0; i < n; i++) {
        a[i] = b[i] * c[i];
    }
}

/* Test 3: int 数组减法 */
void vec_sub_int(int *a, int *b, int *c, int n) {
    for (int i = 0; i < n; i++) {
        a[i] = b[i] - c[i];
    }
}

/* Test 4: float 数组除法 */
void vec_div_float(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) {
        a[i] = b[i] / c[i];
    }
}
