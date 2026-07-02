/* _hydrology.c — 水文计算 C 加速模块。

   实现 D8 流向、水流累积、侵蚀 delta 的批量计算。
   与 _perlin.c 相同的编译/加载模式。

   编译:
       gcc -O3 -shared -fPIC -o _hydrology.so _hydrology.c -lm
*/

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* D8 方向偏移 */
static const int DX[8] = {1, -1, 0, 0, 1, -1, 1, -1};
static const int DY[8] = {0, 0, 1, -1, 1, 1, -1, -1};
static const double DIST[8] = {1.0, 1.0, 1.0, 1.0,
                                1.41421356, 1.41421356, 1.41421356, 1.41421356};

/* ── compute_d8 ─────────────────────────────────────────── */

void hydrology_compute_d8(
    const double *dem, int w, int h, int *directions)
{
    /* 计算 D8 流向。directions 预分配为 w*h 个 int。
       方向编码: 0=E, 1=W, 2=S, 3=N, 4=SE, 5=SW, 6=NE, 7=NW
       无下坡邻居 → -1 */
    int n = w * h;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int idx = y * w + x;
            double elev = dem[idx];
            int best_d = -1;
            double best_slope = -1e100;

            for (int d = 0; d < 8; d++) {
                int nx = x + DX[d];
                int ny = y + DY[d];
                if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
                double ne = dem[ny * w + nx];
                if (ne < elev) {
                    double slope = (elev - ne) / DIST[d];
                    if (slope > best_slope) {
                        best_slope = slope;
                        best_d = d;
                    }
                }
            }
            directions[idx] = best_d;
        }
    }
}

/* ── flow_accumulation ──────────────────────────────────── */

void hydrology_flow_accumulation(
    const int *directions, const double *source,
    int w, int h, double *acc)
{
    /* 水流累积量。source=每像素自身水量（NULL=默认1.0）。
       使用拓扑排序（入度表+BFS队列）。*/
    int n = w * h;

    /* 初始化累积量 = source */
    if (source != NULL) {
        memcpy(acc, source, n * sizeof(double));
    } else {
        for (int i = 0; i < n; i++) acc[i] = 1.0;
    }

    /* 计算入度 */
    int *indegree = (int *)calloc(n, sizeof(int));
    if (!indegree) return;

    for (int i = 0; i < n; i++) {
        int d = directions[i];
        if (d < 0) continue;
        int nx = (i % w) + DX[d];
        int ny = (i / w) + DY[d];
        if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
            indegree[ny * w + nx]++;
        }
    }

    /* BFS 队列（入度为0的点入队） */
    int *queue = (int *)malloc(n * sizeof(int));
    if (!queue) { free(indegree); return; }
    int qhead = 0, qtail = 0;

    for (int i = 0; i < n; i++) {
        if (indegree[i] == 0) {
            queue[qtail++] = i;
        }
    }

    /* 拓扑传播 */
    while (qhead < qtail) {
        int idx = queue[qhead++];
        int d = directions[idx];
        if (d < 0) continue;

        int nx = (idx % w) + DX[d];
        int ny = (idx / w) + DY[d];
        if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;

        int ni = ny * w + nx;
        acc[ni] += acc[idx];
        indegree[ni]--;
        if (indegree[ni] == 0) {
            queue[qtail++] = ni;
        }
    }

    free(indegree);
    free(queue);
}

/* ── erode_step ─────────────────────────────────────────── */

void hydrology_erode_step(
    const double *dem, const int *directions,
    const double *acc, const double *flow_source,
    int w, int h,
    double erodibility,
    double *delta_out, double *sediment_out)
{
    /* 单轮侵蚀：计算每个像素的侵蚀/沉积量。
       delta_out[i] = 侵蚀导致的净海拔变化
       sediment_out[i] = 累积净沉积（增量，调用方负责累加）

       侵蚀量 = K × flow^0.5 × slope^1.0
       限制：不超过 slope * 0.5（不能把山削成坑）
    */
    int n = w * h;
    double m_exp = 0.5;
    double n_exp = 1.0;

    for (int i = 0; i < n; i++) {
        int d = directions[i];
        if (d < 0) continue;  /* 汇点 */

        int x = i % w;
        int y = i / w;
        int nx = x + DX[d];
        int ny = y + DY[d];
        if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;

        int ni = ny * w + nx;
        double slope = dem[i] - dem[ni];
        if (slope <= 0.0) continue;

        double flow = acc[i];
        if (flow < 1.0) flow = 1.0;

        double erosion = erodibility * pow(flow, m_exp) * pow(slope, n_exp);

        /* 限制 */
        double max_erode = slope * 0.5;
        if (erosion > max_erode) erosion = max_erode;

        delta_out[i] -= erosion;
        delta_out[ni] += erosion;

        sediment_out[i] -= erosion;
        sediment_out[ni] += erosion;
    }
}

/* ── hillslope_erosion_step ─────────────────────────────── */

void hydrology_hillslope_step(
    const double *dem, int w, int h,
    double rate, double *delta_out)
{
    /* 山坡扩散侵蚀：陡坡物质向下扩散，圆滑地形。
       只侵蚀陆地（dem > 0）。
    */
    int n = w * h;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int idx = y * w + x;
            double elev = dem[idx];
            if (elev <= 0.0) continue;

            for (int d = 0; d < 8; d++) {
                int nx = x + DX[d];
                int ny = y + DY[d];
                if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;

                int ni = ny * w + nx;
                double ne = dem[ni];
                double diff = elev - ne;
                if (diff > 0.0) {
                    double loss = diff * rate;
                    delta_out[idx] -= loss;
                    delta_out[ni] += loss;
                }
            }
        }
    }
}
