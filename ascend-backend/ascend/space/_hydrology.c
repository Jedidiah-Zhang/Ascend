/* _hydrology.c — 水文计算 C 加速模块。

   实现 D8 流向、水流累积、侵蚀 delta、填洼的批量计算。
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

/* ── 二叉最小堆（用于填洼优先队列） ─────────────────────── */

/* 堆排序键：(elev, idx) 复合键，保证确定性。
   将 elev*1000 量化为 int，与 idx 组成 64 位键避免同高程的不确定性。 */
typedef struct {
    double elev;
    int idx;  /* y*w + x */
} HeapEntry;

typedef struct {
    HeapEntry *data;
    int size;
    int capacity;
} MinHeap;

/* 复合比较：先比 elev，同 elev 时比 idx（确定性平局决胜） */
static int heap_less(HeapEntry *a, HeapEntry *b) {
    if (a->elev != b->elev)
        return a->elev < b->elev;
    return a->idx < b->idx;
}

static MinHeap *heap_create(int capacity) {
    MinHeap *h = (MinHeap *)malloc(sizeof(MinHeap));
    if (!h) return NULL;
    h->data = (HeapEntry *)malloc((size_t)capacity * sizeof(HeapEntry));
    if (!h->data) { free(h); return NULL; }
    h->size = 0;
    h->capacity = capacity;
    return h;
}

static void heap_free(MinHeap *h) {
    if (h) {
        free(h->data);
        free(h);
    }
}

static void heap_push(MinHeap *h, double elev, int idx) {
    /* 上滤 — 容量不足时自动 2× 扩容 */
    if (h->size >= h->capacity) {
        int new_cap = h->capacity * 2;
        HeapEntry *new_data = (HeapEntry *)realloc(
            h->data, (size_t)new_cap * sizeof(HeapEntry));
        if (!new_data) return;  /* OOM — 丢弃（极端情况） */
        h->data = new_data;
        h->capacity = new_cap;
    }
    int i = h->size++;
    HeapEntry entry = {elev, idx};
    while (i > 0) {
        int parent = (i - 1) / 2;
        if (!heap_less(&entry, &h->data[parent])) break;
        h->data[i] = h->data[parent];
        i = parent;
    }
    h->data[i] = entry;
}

static HeapEntry heap_pop(MinHeap *h) {
    /* 下滤 */
    HeapEntry result = h->data[0];
    HeapEntry last = h->data[--h->size];
    int i = 0;
    while (1) {
        int left = 2 * i + 1;
        int right = 2 * i + 2;
        int smallest = i;
        if (left < h->size && heap_less(&h->data[left], &h->data[smallest]))
            smallest = left;
        if (right < h->size && heap_less(&h->data[right], &h->data[smallest]))
            smallest = right;
        if (smallest == i) break;
        h->data[i] = h->data[smallest];
        i = smallest;
    }
    h->data[i] = last;
    return result;
}

/* ── fill_depressions ────────────────────────────────────── */

void hydrology_fill_depressions(
    const double *dem, int w, int h, double *result)
{
    /* 优先队列填洼（Planchon-Darboux 变体）。
       从边界最低点出发向内灌水，确保每个像素都有向边界的下坡路径。
       result 预分配为 w*h 个 double。 */
    int n = w * h;
    memcpy(result, dem, (size_t)n * sizeof(double));

    /* 已处理标记 */
    unsigned char *processed = (unsigned char *)calloc((size_t)n, 1);
    if (!processed) return;

    /* 最坏情况所有像素入堆 */
    MinHeap *heap = heap_create(n);
    if (!heap) { free(processed); return; }

    /* 所有边界像素入堆 */
    for (int x = 0; x < w; x++) {
        for (int y = 0; y < h; y += (h - 1)) {
            int idx = y * w + x;
            heap_push(heap, dem[idx], idx);
            processed[idx] = 1;
        }
    }
    for (int y = 1; y < h - 1; y++) {
        for (int x = 0; x < w; x += (w - 1)) {
            int idx = y * w + x;
            heap_push(heap, dem[idx], idx);
            processed[idx] = 1;
        }
    }

    /* 从最低边界点向内蔓延 */
    while (heap->size > 0) {
        HeapEntry e = heap_pop(heap);
        int x = e.idx % w;
        int y = e.idx / w;
        double spill = e.elev + 0.001;

        for (int d = 0; d < 8; d++) {
            int nx = x + DX[d];
            int ny = y + DY[d];
            if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
            int ni = ny * w + nx;
            if (processed[ni]) continue;

            if (result[ni] < spill)
                result[ni] = spill;

            processed[ni] = 1;
            heap_push(heap, result[ni], ni);
        }
    }

    heap_free(heap);
    free(processed);
}

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

/* ── apply_erosion ────────────────────────────────────────── */

double hydrology_apply_erosion(
    double *dem,
    double *sediment_net,
    const double *delta,
    int n)
{
    /* 将侵蚀 delta 应用到 dem 和 sediment_net，返回 max|delta|。
       替代 Python 层的逐元素循环，消除 12 轮 × 600K 的 Python 迭代开销。
    */
    double max_delta = 0.0;
    for (int i = 0; i < n; i++) {
        double d = delta[i];
        dem[i] += d;
        sediment_net[i] += d;
        double abs_d = d >= 0.0 ? d : -d;
        if (abs_d > max_delta) max_delta = abs_d;
    }
    return max_delta;
}

/* ── gaussian_blur ────────────────────────────────────────── */

void hydrology_gaussian_blur(
    const double *arr, int w, int h, double sigma,
    double *result)
{
    /* 可分离 2-pass 高斯模糊，边界钳制到边缘。
       sigma 以格为单位。替代 Python 层的 12M 次浮点运算。
    */
    int radius = (int)(3.0 * sigma);
    if (radius < 1) radius = 1;
    int kw = 2 * radius + 1;

    /* 构建 kernel */
    double *kernel = (double *)malloc(kw * sizeof(double));
    double ks = 0.0;
    for (int i = 0; i < kw; i++) {
        double t = (double)(i - radius) / sigma;
        kernel[i] = exp(-0.5 * t * t);
        ks += kernel[i];
    }
    for (int i = 0; i < kw; i++) kernel[i] /= ks;

    /* 水平方向 pass → tmp */
    double *tmp = (double *)malloc((size_t)w * h * sizeof(double));
    for (int y = 0; y < h; y++) {
        int row = y * w;
        for (int x = 0; x < w; x++) {
            double s = 0.0;
            for (int i = 0; i < kw; i++) {
                int xi = x + i - radius;
                if (xi < 0) xi = 0;
                else if (xi >= w) xi = w - 1;
                s += kernel[i] * arr[row + xi];
            }
            tmp[row + x] = s;
        }
    }

    /* 垂直方向 pass → result */
    for (int x = 0; x < w; x++) {
        for (int y = 0; y < h; y++) {
            double s = 0.0;
            for (int i = 0; i < kw; i++) {
                int yi = y + i - radius;
                if (yi < 0) yi = 0;
                else if (yi >= h) yi = h - 1;
                s += kernel[i] * tmp[yi * w + x];
            }
            result[(size_t)y * w + x] = s;
        }
    }

    free(kernel);
    free(tmp);
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

/* ── dijkstra_to_ocean ────────────────────────────────────── */

void hydrology_dijkstra_to_ocean(
    const double *dem, const double *flow_acc,
    int w, int h, double *dist)
{
    /* 多源 Dijkstra：从所有海洋格（dem < 0）出发，计算每格到海的
       最低累积代价。代价 = max(0, dem[i]) + 1 - 1.5*log1p(flow_acc[i])。

       复用二叉最小堆，以 dist 作为 HeapEntry.elev 排序键。
       替代 Python 层 ~500 万次 heapq 操作。
    */
    int n = w * h;
    double INF = 1e100;

    /* 初始化：所有海洋格 dist=0，但只推入海陆边界格（有陆地邻居的海洋格）。
       内部海洋格永远不会被更优路径访问到（dist 已是 0，所有边代价 >0），
       跳过它们大幅减少堆操作（海洋占 ~45%，其中边界仅 ~5%）。 */
    MinHeap *heap = heap_create(n / 10 + 1024);  /* 保守初始容量 */
    int boundary_pushed = 0;
    for (int i = 0; i < n; i++) {
        if (dem[i] < 0.0) {
            dist[i] = 0.0;
            /* 仅当有陆地邻居时才入堆 */
            int x = i % w;
            int y = i / w;
            int has_land = 0;
            for (int k = 0; k < 8; k++) {
                int nx = x + DX[k];
                int ny = y + DY[k];
                if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
                    if (dem[ny * w + nx] >= 0.0) { has_land = 1; break; }
                }
            }
            if (has_land) {
                heap_push(heap, 0.0, i);
                boundary_pushed++;
            }
        } else {
            dist[i] = INF;
        }
    }

    /* 主循环 */
    while (heap->size > 0) {
        HeapEntry entry = heap_pop(heap);
        double d = entry.elev;
        int i = entry.idx;
        if (d > dist[i]) continue;  /* 过期条目 */

        int x = i % w;
        int y = i / w;

        for (int k = 0; k < 8; k++) {
            int nx = x + DX[k];
            int ny = y + DY[k];
            if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;

            int ni = ny * w + nx;
            double elev_cost = (dem[ni] > 0.0 ? dem[ni] : 0.0) + 1.0;
            double flow_bonus = 1.5 * log1p(flow_acc[ni]);
            double step = elev_cost - flow_bonus;
            if (step < 0.1) step = 0.1;

            double nd = d + step;
            /* 仅当改进显著时才更新（避免 0.1 量级的重复推送） */
            if (nd + 1e-12 < dist[ni]) {
                dist[ni] = nd;
                heap_push(heap, nd, ni);
            }
        }
    }

    heap_free(heap);
}

/* ── rain_shadow ──────────────────────────────────────────── */

void hydrology_rain_shadow(
    const double *elevation, int w, int h,
    int scan_axis, int windward,
    double *factors)
{
    /* 沿主轴扫描计算雨影因子。
       scan_axis: 0=沿行扫描（东西风向），1=沿列扫描（南北风向）
       windward: 0=迎风侧在起点，1=迎风侧在终点
       滑动窗口 W=40 格，替代 Python 层 ~600K 双循环开销。
    */
    const int WINDOW = 40;
    int outer = (scan_axis == 0) ? h : w;
    int inner = (scan_axis == 0) ? w : h;

    double *pref = (double *)malloc((size_t)inner * sizeof(double));

    for (int o = 0; o < outer; o++) {
        /* 前缀和：沿扫描方向累加上坡量 */
        double running = 0.0;
        for (int i = 0; i < inner; i++) {
            int idx, prev_idx, next_idx, j;
            double gain = 0.0;

            if (scan_axis == 0) {
                if (windward == 0) {
                    idx = o * w + i;
                    prev_idx = o * w + (i - 1);
                    if (i > 0) gain = elevation[prev_idx] - elevation[idx];
                } else {
                    j = inner - 1 - i;
                    idx = o * w + j;
                    next_idx = o * w + (j + 1);
                    if (j < inner - 1) gain = elevation[next_idx] - elevation[idx];
                }
            } else {
                if (windward == 0) {
                    idx = i * w + o;
                    prev_idx = (i - 1) * w + o;
                    if (i > 0) gain = elevation[prev_idx] - elevation[idx];
                } else {
                    j = inner - 1 - i;
                    idx = j * w + o;
                    next_idx = (j + 1) * w + o;
                    if (j < inner - 1) gain = elevation[next_idx] - elevation[idx];
                }
            }

            if (gain > 0.0) running += gain;
            pref[i] = running;
        }

        /* 滑动窗口 → 雨影因子 */
        for (int i = 0; i < inner; i++) {
            int idx;
            if (scan_axis == 0) {
                if (windward == 0)
                    idx = o * w + i;
                else
                    idx = o * w + (inner - 1 - i);
            } else {
                if (windward == 0)
                    idx = i * w + o;
                else
                    idx = (inner - 1 - i) * w + o;
            }

            double total_uplift = (i <= WINDOW) ? pref[i] : pref[i] - pref[i - WINDOW];
            double f;
            if (total_uplift < 30.0)
                f = 1.0;
            else if (total_uplift < 150.0)
                f = 1.0 - (total_uplift - 30.0) / 120.0 * 0.4;
            else if (total_uplift < 400.0)
                f = 0.6 - (total_uplift - 150.0) / 250.0 * 0.35;
            else {
                f = 0.25 - (total_uplift - 400.0) / 2000.0 * 0.15;
                if (f < 0.15) f = 0.15;
            }
            factors[idx] = f;
        }
    }

    free(pref);
}

/* ── compute_climate ──────────────────────────────────────── */

/* climate classification constants */
#define LAPSE_RATE 9.0
#define RAINFALL_MIN 50.0
#define RAINFALL_MAX 3500.0
#define ALPINE_ALT 2000.0
#define POLAR_TEMP -5.0
#define DESERT_RAIN 200.0
#define STEPPE_RAIN 600.0
#define STEPPE_MIN_T 5.0
#define TROPICAL_T 20.0
#define TEMPERATE_T 5.0
#define RAINFOREST_RAIN 1500.0
#define TAIGA_RAIN 400.0

static int classify_climate(double temp, double rainfall, double altitude) {
    /* 8-zone climate classification, mirrors climate.py:classify() */
    if (altitude >= ALPINE_ALT) return 7;        /* ALPINE */
    if (temp < POLAR_TEMP) return 6;              /* POLAR_TUNDRA */
    if (rainfall < DESERT_RAIN) return 2;          /* DESERT */
    if (rainfall < STEPPE_RAIN && temp > STEPPE_MIN_T) return 3; /* STEPPE */
    if (temp >= TROPICAL_T) {
        if (rainfall >= RAINFOREST_RAIN) return 0;  /* EQUATORIAL_RAINFOREST */
        return 1;                                  /* TROPICAL_SAVANNA */
    }
    if (temp >= TEMPERATE_T) return 4;             /* TEMPERATE_FOREST */
    if (rainfall >= TAIGA_RAIN) return 5;           /* SUBARCTIC_TAIGA */
    return 6;                                      /* POLAR_TUNDRA */
}

static double rainfall_from_noise_c(double n) {
    /* noise [-1,1] -> rainfall mm/yr, matches climate.py */
    double r = RAINFALL_MIN + (n + 1.0) * 0.5 * (RAINFALL_MAX - RAINFALL_MIN);
    if (r < 0.0) r = 0.0;
    if (r > 5000.0) r = 5000.0;
    return r;
}

void hydrology_compute_climate(
    const double *elevation,
    const double *lat_wiggle, const double *rain_raw,
    const double *rain_shadow,
    int w, int h,
    double gx, double gy,
    double *temp_out, double *rain_out, int *climate_out)
{
    /* 单次 C 遍历替代 Python 600K 循环。
       温度 = 纬度梯度 + 微量摆动 - 海拔 × 直减率
       降雨 = 降雨噪声 × 雨影因子
       气候 = classify()
    */
    double inv_w = 1.0 / (double)w;
    double inv_h = 1.0 / (double)h;
    double lapse = LAPSE_RATE / 1000.0;
    int n = w * h;

    for (int i = 0; i < n; i++) {
        int x = i % w;
        int y = i / w;
        double px = ((double)x * inv_w - 0.5) * 2.0;
        double py = ((double)y * inv_h - 0.5) * 2.0;
        double lat_n = (px * gx + py * gy) * 0.6 + lat_wiggle[i] * 0.15;

        double sea_temp = lat_n * 25.0 + 10.0;
        if (sea_temp < -20.0) sea_temp = -20.0;
        if (sea_temp > 38.0) sea_temp = 38.0;

        double elev = elevation[i];
        double temp = sea_temp - elev * lapse;
        if (temp < -20.0) temp = -20.0;
        if (temp > 36.0) temp = 36.0;

        double rain_n = rain_raw[i];
        double rainfall = rainfall_from_noise_c(rain_n) * rain_shadow[i];

        temp_out[i] = temp;
        rain_out[i] = rainfall;
        climate_out[i] = classify_climate(temp, rainfall, elev);
    }
}
