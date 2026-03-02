/* test_reduction.c - 归约运算测试 */

/* Test 1: float 数组求和 */
float array_sum(float *a, int n) {
    float sum = 0.0f;
    for (int i = 0; i < n; i++) {
        sum += a[i];
    }
    return sum;
}

/* Test 2: int 数组求和 */
int array_sum_int(int *a, int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        total += a[i];
    }
    return total;
}

/* Test 3: double 数组求和 */
double array_sum_double(double *a, int n) {
    double acc = 0.0;
    for (int i = 0; i < n; i++) {
        acc += a[i];
    }
    return acc;
}
