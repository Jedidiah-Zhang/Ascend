/* _tectonic.c — Voronoi 构造海拔批量计算的 C 加速。

   用法同 _perlin.c: gcc -O3 -shared -fPIC -o _tectonic.so _tectonic.c -lm

   输入: 预计算的单元属性数组 (cx, cy, elev, drx, dry)
   输出: 批量海拔数组 (行优先)
*/

#include <math.h>
#include <stdlib.h>

#define MAX_CELLS 49  /* 7x7 max */


void tectonic_altitude_batch_c(
    const double* cell_cx, const double* cell_cy,
    const double* cell_elev, const double* cell_drx, const double* cell_dry,
    int n_cells,
    int world_x, int world_y, int w, int h,
    double sigma2, double drift_scale, double uplift_scale,
    double altitude_floor, double altitude_ceil,
    double* output
) {
    double inv_2ds = 1.0 / (2.0 * drift_scale + 1e-6);
    int size = w * h;

    for (int ty = 0; ty < h; ty++) {
        int wy = world_y + ty;
        for (int tx = 0; tx < w; tx++) {
            int wx = world_x + tx;

            /* 高斯加权平均海拔 + 追踪最重 2 个单元 */
            double weighted_sum = 0.0;
            double weight_sum = 0.0;
            double best_w1 = -1.0, best_w2 = -1.0;
            int best_i1 = 0, best_i2 = 0;

            for (int ci = 0; ci < n_cells; ci++) {
                double dx = (double)wx - cell_cx[ci];
                double dy = (double)wy - cell_cy[ci];
                double d2 = dx * dx + dy * dy;
                double w = exp(-d2 / sigma2);
                weighted_sum += cell_elev[ci] * w;
                weight_sum += w;
                if (w > best_w1) {
                    best_w2 = best_w1; best_i2 = best_i1;
                    best_w1 = w;       best_i1 = ci;
                } else if (w > best_w2) {
                    best_w2 = w;       best_i2 = ci;
                }
            }

            double altitude = weighted_sum / (weight_sum + 1e-10);

            /* 收敛隆起：最重 2 个单元之间 */
            double cx1 = cell_cx[best_i1], cy1 = cell_cy[best_i1];
            double cx2 = cell_cx[best_i2], cy2 = cell_cy[best_i2];
            double abx = cx2 - cx1, aby = cy2 - cy1;
            double dist_ab = sqrt(abx * abx + aby * aby);

            if (dist_ab > 1e-6) {
                double nx = abx / dist_ab, ny = aby / dist_ab;
                double rel_vx = cell_drx[best_i2] - cell_drx[best_i1];
                double rel_vy = cell_dry[best_i2] - cell_dry[best_i1];
                double v_proj = rel_vx * nx + rel_vy * ny;
                double convergence = (-v_proj > 0.0) ? (-v_proj * inv_2ds) : 0.0;
                if (convergence > 1.0) convergence = 1.0;

                double d1 = sqrt((wx - cx1) * (wx - cx1) + (wy - cy1) * (wy - cy1));
                double d2 = sqrt((wx - cx2) * (wx - cx2) + (wy - cy2) * (wy - cy2));
                double denom = d1 + d2;
                double t = d1 / (denom + 1e-10);
                double boundary_t = exp(-((t - 0.5) * (t - 0.5)) / 0.25);

                altitude += convergence * boundary_t * uplift_scale;
            }

            if (altitude < altitude_floor) altitude = altitude_floor;
            if (altitude > altitude_ceil)  altitude = altitude_ceil;

            output[ty * w + tx] = altitude;
        }
    }
}
