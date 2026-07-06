/* _streamlines.c — 流线追踪模块。

   实现双线性插值、负梯度方向场、三级混合方向场、
   RK4 积分步进、以及沿方向场向下游追踪到海的完整流线。

   编译:
       gcc -O3 -shared -fPIC -o _streamlines.so _streamlines.c -lm
*/

#include <math.h>

/* ═══════════════════════════════════════════════════════════════
   双线性插值 — 匹配 Python _bilinear 的边界行为。
   对整数索引钳制到 [0, w-2]，允许 fx/fy 在边缘外插。
   ═══════════════════════════════════════════════════════════════ */

static double _bilinear(const double *arr, int w, int h,
                         double x, double y) {
    int ix = (int)x;
    if (ix < 0) ix = 0;
    if (ix > w - 2) ix = w - 2;
    int iy = (int)y;
    if (iy < 0) iy = 0;
    if (iy > h - 2) iy = h - 2;

    double fx = x - (double)ix;
    double fy = y - (double)iy;

    int row0 = iy * w;
    int row1 = row0 + w;
    double a = arr[row0 + ix];
    double b = arr[row0 + ix + 1];
    double c = arr[row1 + ix];
    double d = arr[row1 + ix + 1];

    double w1 = 1.0 - fx;
    return (w1 * (1.0 - fy) * a + fx * (1.0 - fy) * b +
            w1 * fy * c + fx * fy * d);
}

/* ═══════════════════════════════════════════════════════════════
   中心差分计算 -∇arr（指向 arr 下降最快方向），eps=0.75。
   ═══════════════════════════════════════════════════════════════ */

static void _neg_grad(double x, double y, const double *arr,
                       int w, int h, double eps,
                       double *out_gx, double *out_gy) {
    double inv_2eps = 1.0 / (2.0 * eps);
    double gx = (_bilinear(arr, w, h, x + eps, y) -
                 _bilinear(arr, w, h, x - eps, y)) * inv_2eps;
    double gy = (_bilinear(arr, w, h, x, y + eps) -
                 _bilinear(arr, w, h, x, y - eps)) * inv_2eps;
    *out_gx = -gx;
    *out_gy = -gy;
}

/* ═══════════════════════════════════════════════════════════════
   三级混合方向场。

   Tier 1: dem 梯度强 + 与 dist 下降同向 → 跟 dem（山谷弯曲）
   Tier 2: dem 弱/反向 → flow_acc +梯度（指向下游主流通道），
           也需与 dist 同向否则退 dist
   Tier 3: dist 下降方向兜底
   全失效返回 -1。
   ═══════════════════════════════════════════════════════════════ */

static int _flow_dir(double x, double y,
                      const double *smooth_dem,
                      const double *smooth_flow,
                      const double *dist,
                      int w, int h,
                      double dem_min, double flow_min,
                      double *out_dx, double *out_dy) {
    double gxd, gyd, gxf, gyf;
    _neg_grad(x, y, smooth_dem, w, h, 0.75, &gxd, &gyd);
    double md = hypot(gxd, gyd);

    _neg_grad(x, y, dist, w, h, 0.75, &gxf, &gyf);
    double mf = hypot(gxf, gyf);

    /* Tier 1: DEM 梯度（山谷跟随） */
    if (md > dem_min) {
        if (mf > 1e-6) {
            if (gxd * gxf + gyd * gyf > 0.0) {
                *out_dx = gxd / md;
                *out_dy = gyd / md;
                return 0;
            }
        } else {
            *out_dx = gxd / md;
            *out_dy = gyd / md;
            return 0;
        }
    }

    /* Tier 2: flow_acc +梯度（指向下游高流量主流通道） */
    double inv_1p5 = 1.0 / 1.5;
    double gfx = (_bilinear(smooth_flow, w, h, x + 0.75, y) -
                  _bilinear(smooth_flow, w, h, x - 0.75, y)) * inv_1p5;
    double gfy = (_bilinear(smooth_flow, w, h, x, y + 0.75) -
                  _bilinear(smooth_flow, w, h, x, y - 0.75)) * inv_1p5;
    double mfa = hypot(gfx, gfy);

    if (mfa > flow_min) {
        if (mf > 1e-6) {
            if (gfx * gxf + gfy * gyf > 0.0) {
                *out_dx = gfx / mfa;
                *out_dy = gfy / mfa;
                return 0;
            }
            *out_dx = gxf / mf;
            *out_dy = gyf / mf;
            return 0;
        }
        *out_dx = gfx / mfa;
        *out_dy = gfy / mfa;
        return 0;
    }

    /* Tier 3: dist 梯度兜底 */
    if (mf > 1e-6) {
        *out_dx = gxf / mf;
        *out_dy = gyf / mf;
        return 0;
    }

    return -1;
}

/* ═══════════════════════════════════════════════════════════════
   RK4 积分一步，返回 0=成功，-1=终止。
   ═══════════════════════════════════════════════════════════════ */

static int _rk4_step(double x, double y,
                      const double *smooth_dem,
                      const double *smooth_flow,
                      const double *dist,
                      int w, int h,
                      double ds, double dem_min, double flow_min,
                      double *out_x, double *out_y) {
    double k1x, k1y, k2x, k2y, k3x, k3y, k4x, k4y;

    if (_flow_dir(x, y, smooth_dem, smooth_flow, dist, w, h,
                   dem_min, flow_min, &k1x, &k1y) != 0)
        return -1;

    if (_flow_dir(x + 0.5 * ds * k1x, y + 0.5 * ds * k1y,
                   smooth_dem, smooth_flow, dist, w, h,
                   dem_min, flow_min, &k2x, &k2y) != 0)
        return -1;

    if (_flow_dir(x + 0.5 * ds * k2x, y + 0.5 * ds * k2y,
                   smooth_dem, smooth_flow, dist, w, h,
                   dem_min, flow_min, &k3x, &k3y) != 0)
        return -1;

    if (_flow_dir(x + ds * k3x, y + ds * k3y,
                   smooth_dem, smooth_flow, dist, w, h,
                   dem_min, flow_min, &k4x, &k4y) != 0)
        return -1;

    *out_x = x + ds * (k1x + 2.0 * k2x + 2.0 * k3x + k4x) / 6.0;
    *out_y = y + ds * (k1y + 2.0 * k2y + 2.0 * k3y + k4y) / 6.0;
    return 0;
}

/* ═══════════════════════════════════════════════════════════════
   从源头沿混合方向场 RK4 追踪到海。

   参数:
     src_idx:   源头网格索引 (y*w + x)。
     dem:       侵蚀后海拔 (<0=海洋)。
     smooth_dem, smooth_flow, dist: 三个方向场（均为 w*h 双精度数组）。
     w, h:      网格尺寸。
     max_steps: 最大追踪步数。
     step_size: RK4 步长。
     dem_min, flow_min: _flow_dir 阈值。

   输出:
     out_x, out_y: 调用者预分配的 double 数组（至少 max_steps 长度）。
     返回追踪到的点数（包含源头），到达海洋、越界、或停滞时停止。
   ═══════════════════════════════════════════════════════════════ */

int streamlines_trace_downstream(
    int src_idx,
    const double *dem,
    const double *smooth_dem,
    const double *smooth_flow,
    const double *dist,
    int w, int h,
    int max_steps, double step_size,
    double dem_min, double flow_min,
    double *out_x, double *out_y)
{
    double x = (double)(src_idx % w);
    double y = (double)(src_idx / w);
    int count = 0;

    for (int step = 0; step < max_steps; step++) {
        int ix = (int)x;
        int iy = (int)y;
        if (ix < 0 || ix >= w || iy < 0 || iy >= h)
            break;

        out_x[count] = x;
        out_y[count] = y;
        count++;

        /* 到达海洋 */
        if (dem[iy * w + ix] < 0.0)
            break;

        double nx, ny;
        if (_rk4_step(x, y, smooth_dem, smooth_flow, dist, w, h,
                       step_size, dem_min, flow_min, &nx, &ny) != 0)
            break;

        double dx = nx - x;
        double dy = ny - y;
        if (dx * dx + dy * dy < 1e-8)
            break;

        x = nx;
        y = ny;
    }

    return count;
}
