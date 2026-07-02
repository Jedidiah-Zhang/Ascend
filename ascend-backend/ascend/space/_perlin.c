/* _perlin.c — Perlin 噪声 C 实现（无状态）

   编译: gcc -O3 -shared -fPIC -o _perlin.so _perlin.c -lm
*/

#include <math.h>

static const double GRADS[8][2] = {
    { 1.0,  1.0}, {-1.0,  1.0}, { 1.0, -1.0}, {-1.0, -1.0},
    { 1.0,  0.0}, {-1.0,  0.0}, { 0.0,  1.0}, { 0.0, -1.0},
};

static inline double _fade(double t) {
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
}

static inline double _lerp(double a, double b, double t) {
    return a + t * (b - a);
}

static inline double _grad(int hash, double x, double y) {
    int h = hash & 7;
    return GRADS[h][0] * x + GRADS[h][1] * y;
}

static inline double _noise2d(const int perm[512], double x, double y) {
    int xi = (int)floor(x) & 255;
    int yi = (int)floor(y) & 255;
    double xf = x - floor(x);
    double yf = y - floor(y);
    double u = _fade(xf);
    double v = _fade(yf);

    int aa = perm[perm[xi] + yi];
    int ab = perm[perm[xi] + yi + 1];
    int ba = perm[perm[xi + 1] + yi];
    int bb = perm[perm[xi + 1] + yi + 1];

    double x1 = _lerp(_grad(aa, xf, yf), _grad(ba, xf - 1.0, yf), u);
    double x2 = _lerp(_grad(ab, xf, yf - 1.0), _grad(bb, xf - 1.0, yf - 1.0), u);
    return _lerp(x1, x2, v);
}

double perlin_sample(const int perm[512], double x, double y) {
    return _noise2d(perm, x, y);
}

double perlin_octave(const int perm[512], double x, double y,
                     int octaves, double persistence,
                     double lacunarity, double frequency) {
    double total = 0.0, amp = 1.0, max_val = 0.0, freq = frequency;
    for (int i = 0; i < octaves; i++) {
        total += _noise2d(perm, x * freq, y * freq) * amp;
        max_val += amp;
        amp *= persistence;
        freq *= lacunarity;
    }
    return total / max_val;
}

/* ── 批量网格接口 ──────────────────────────────────────────── */

void perlin_octave_grid(const int perm[512],
                        double cx, double cy, int w, int h,
                        double frequency,
                        double *output,
                        int octaves, double persistence,
                        double lacunarity) {
    /* 在网格上批量采样多八度噪声，避免逐像素 ctypes 开销。
       cx, cy 为浮点起始坐标，可加 0.5 偏移避开整数网格点（噪声零点）。
       output 必须预分配 w*h 个 double。 */
    for (int row = 0; row < h; row++) {
        for (int col = 0; col < w; col++) {
            double x = (cx + (double)col) * frequency;
            double y = (cy + (double)row) * frequency;
            double total = 0.0, amp = 1.0, max_val = 0.0;
            double freq = 1.0;
            for (int i = 0; i < octaves; i++) {
                total += _noise2d(perm, x * freq, y * freq) * amp;
                max_val += amp;
                amp *= persistence;
                freq *= lacunarity;
            }
            output[row * w + col] = total / max_val;
        }
    }
}

