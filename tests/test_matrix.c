/* test_matrix.c - 矩阵运算测试（行主序，平铺数组） */

/* Test 1: 矩阵乘法 C[M][N] = A[M][K] * B[K][N] */
void matmul(float *C, float *A, float *B, int M, int N, int K) {
    for (int i = 0; i < M; i++) {
        for (int k = 0; k < K; k++) {
            for (int j = 0; j < N; j++) {
                C[i*N+j] += A[i*K+k] * B[k*N+j];
            }
        }
    }
}

/* Test 2: 矩阵向量乘 y[M] = A[M][N] * x[N] */
void matvec(float *y, float *A, float *x, int M, int N) {
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            y[i] += A[i*N+j] * x[j];
        }
    }
}
