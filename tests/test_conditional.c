/* test_conditional.c - 带条件循环测试 */

/* Test 1: ReLU (if/else) */
void relu(float *a, float *b, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] > 0.0f) {
            b[i] = a[i];
        } else {
            b[i] = 0.0f;
        }
    }
}

/* Test 2: 单分支阈值（无 else） */
void threshold(float *a, float *b, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] > 0.5f) {
            b[i] = a[i];
        }
    }
}

/* Test 3: 负值清零 */
void clamp_negative(float *a, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] < 0.0f) {
            a[i] = 0.0f;
        }
    }
}
