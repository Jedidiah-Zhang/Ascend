/* _hydrology.c — 水文计算 C 加速模块。

   实现 D8 流向、水流累积、侵蚀 delta、填洼的批量计算。
   与 _perlin.c 相同的编译/加载模式。

   编译:
       gcc -O3 -shared -fPIC -o _hydrology.so _hydrology.c -lm
*/

#include <limits.h>
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
    /* 下滤：始终用 last 与较小子节点比较，找到其最终位置后放入。 */
    HeapEntry result = h->data[0];
    HeapEntry last = h->data[--h->size];
    int i = 0;
    while (1) {
        int left = 2 * i + 1;
        int right = 2 * i + 2;
        if (left >= h->size) break;  /* 无子节点 */
        int sc = left;
        if (right < h->size && heap_less(&h->data[right], &h->data[left]))
            sc = right;
        if (!heap_less(&h->data[sc], &last)) break;
        h->data[i] = h->data[sc];
        i = sc;
    }
    h->data[i] = last;
    return result;
}

/* ── fill_depressions ────────────────────────────────────── */

void hydrology_fill_depressions(
    const double *dem, int w, int h, double *result)
{
    /* Planchon-Darboux 填洼（海洋为边界，单次访问 + 堆保证最优）。
       海洋格 (dem < 0) 作为固定边界入堆，陆地向内传播。
       堆按水位升序弹出，保证每个格首次被访问时即得到最低水位路径。 */
    int n = w * h;
    for (int i = 0; i < n; i++) {
        result[i] = dem[i];
    }

    unsigned char *processed = (unsigned char *)calloc((size_t)n, 1);
    if (!processed) return;

    MinHeap *heap = heap_create(n);
    if (!heap) { free(processed); return; }

    /* 仅海洋格入堆作为边界 */
    for (int i = 0; i < n; i++) {
        if (dem[i] < 0.0) {
            heap_push(heap, dem[i], i);
            processed[i] = 1;
        }
    }

    /* 从最低海洋格向外单次传播 */
    while (heap->size > 0) {
        HeapEntry e = heap_pop(heap);
        int idx = e.idx;
        int x = idx % w;
        int y = idx / w;
        double spill = e.elev + 0.001;

        for (int d = 0; d < 8; d++) {
            int nx = x + DX[d];
            int ny = y + DY[d];
            if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
            int ni = ny * w + nx;
            if (processed[ni]) continue;

            if (result[ni] < spill) {
                result[ni] = spill;
            }

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

/* ── distance_to_ocean ────────────────────────────────────── */

void hydrology_distance_to_ocean(
    const double *elevation, int w, int h,
    double *dist_out)
{
    /* 多源 BFS 计算每个陆地格到最近海洋的 Chebyshev 距离（格数）。
       海洋格（dem < 0）距离为 0，陆地距离 = 到最近海洋的 D8 步数。
       循环队列，O(N)，每个格入队/出队一次。
    */
    int n = w * h;
    int *dist = (int *)malloc((size_t)n * sizeof(int));
    int *queue = (int *)malloc((size_t)n * sizeof(int));
    int head = 0, tail = 0;

    /* 初始化：海洋距离 0 并入队，陆地初始化为大数 */
    for (int i = 0; i < n; i++) {
        if (elevation[i] < 0.0) {
            dist[i] = 0;
            queue[tail++] = i;
        } else {
            dist[i] = INT_MAX / 2;
        }
    }

    /* BFS */
    while (head < tail) {
        int idx = queue[head++];
        int x = idx % w;
        int y = idx / w;
        int nd = dist[idx] + 1;

        for (int d = 0; d < 8; d++) {
            int nx = x + DX[d];
            int ny = y + DY[d];
            if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
            int ni = ny * w + nx;
            if (nd < dist[ni]) {
                dist[ni] = nd;
                queue[tail++] = ni;
            }
        }
    }

    /* 转换为 double 输出 */
    for (int i = 0; i < n; i++) {
        dist_out[i] = (double)dist[i];
    }

    free(dist);
    free(queue);
}

/* ── rain_shadow (omnidirectional moisture budget) ────────── */

/* 投影排序条目：风向投影 + 原始索引 */
typedef struct {
    double proj;
    int idx;
} ProjEntry;

static int _proj_compare(const void *a, const void *b) {
    double pa = ((const ProjEntry *)a)->proj;
    double pb = ((const ProjEntry *)b)->proj;
    if (pa < pb) return -1;
    if (pa > pb) return 1;
    /* 平局决胜：idx 保证确定性输出（qsort 非稳定排序） */
    int ia = ((const ProjEntry *)a)->idx;
    int ib = ((const ProjEntry *)b)->idx;
    return (ia < ib) ? -1 : (ia > ib) ? 1 : 0;
}

/* 双线性插值，边界钳制（不在网格内则钳到边缘像素） */
static double _bilinear_clamp(const double *grid, int w, int h,
                               double x, double y) {
    if (x < 0.0) x = 0.0;
    if (x >= (double)(w - 1)) x = (double)(w - 1) - 1e-10;
    if (y < 0.0) y = 0.0;
    if (y >= (double)(h - 1)) y = (double)(h - 1) - 1e-10;

    int x0 = (int)x;
    int y0 = (int)y;
    int x1 = x0 + 1;
    int y1 = y0 + 1;

    double fx = x - (double)x0;
    double fy = y - (double)y0;

    double v00 = grid[y0 * w + x0];
    double v10 = grid[y0 * w + x1];
    double v01 = grid[y1 * w + x0];
    double v11 = grid[y1 * w + x1];

    double v0 = v00 + (v10 - v00) * fx;
    double v1 = v01 + (v11 - v01) * fx;
    return v0 + (v1 - v0) * fy;
}

/* ── 抬升 → 雨影因子映射（与旧 1D 算法相同，保证平滑）────── */

static double _uplift_to_factor(double total_uplift, double min_factor) {
    /* 分段线性：累积抬升越大 → 因子越小。
       阈值与旧 hydrology_rain_shadow 一致，保证输出连续。 */
    if (total_uplift < 30.0)
        return 1.0;
    else if (total_uplift < 150.0)
        return 1.0 - (total_uplift - 30.0) / 120.0 * 0.4;
    else if (total_uplift < 400.0)
        return 0.6 - (total_uplift - 150.0) / 250.0 * 0.35;
    else {
        double f = 0.25 - (total_uplift - 400.0) / 2000.0 * 0.15;
        return (f < min_factor) ? min_factor : f;
    }
}

/* 前向声明 */
static void _gaussian_blur_inplace(double *arr, int w, int h, double sigma);

/* 单风向抬升累积追踪 — 排序扫描 DP + 指数衰减 */
static void _rain_shadow_single_dir(
    const double *elevation, int w, int h,
    double angle_rad,
    double decay_length_km, double cell_size_km, double min_factor,
    double *factors)
{
    int n = w * h;
    double wx = cos(angle_rad);
    double wy = sin(angle_rad);

    /* 归一化步长 ≈ 1 格，配合最近邻采样（避免双线性自依赖） */
    double norm = fmax(1e-10, fmax(fabs(wx), fabs(wy)));
    double step_scale = 1.0 / norm;
    double step_x = wx * step_scale;
    double step_y = wy * step_scale;

    /* 每步的指数衰减系数 */
    double step_mag = sqrt(step_x * step_x + step_y * step_y);
    double step_km = step_mag * cell_size_km;
    double decay = exp(-step_km / decay_length_km);

    /* 分配 */
    ProjEntry *entries = (ProjEntry *)malloc((size_t)n * sizeof(ProjEntry));
    double *uplift_eff = (double *)malloc((size_t)n * sizeof(double));

    /* 计算投影 */
    for (int i = 0; i < n; i++) {
        entries[i].idx = i;
        entries[i].proj = (double)(i % w) * wx + (double)(i / w) * wy;
    }

    /* 按投影升序（上风 → 下风） */
    qsort(entries, n, sizeof(ProjEntry), _proj_compare);

    /* DP 扫描 */
    for (int j = 0; j < n; j++) {
        int idx = entries[j].idx;
        int x = idx % w;
        int y = idx / w;

        double src_x = (double)x - step_x;
        double src_y = (double)y - step_y;

        double src_uplift;
        double src_elev;

        if (src_x < 0.0 || src_x >= (double)(w - 1) ||
            src_y < 0.0 || src_y >= (double)(h - 1)) {
            /* 出界 → 开洋，抬升归零 */
            src_uplift = 0.0;
            src_elev = 0.0;
        } else {
            src_elev = _bilinear_clamp(elevation, w, h, src_x, src_y);
            if (src_elev < 0.0) {
                /* 上风是海洋 → 抬升归零 */
                src_uplift = 0.0;
            } else {
                /* 最近邻采样（步长=1时避免自依赖，且比双线性更好处理海岸） */
                int sx = (int)(src_x + 0.5);
                int sy = (int)(src_y + 0.5);
                if (sx < 0) sx = 0;
                if (sx >= w) sx = w - 1;
                if (sy < 0) sy = 0;
                if (sy >= h) sy = h - 1;
                src_uplift = uplift_eff[sy * w + sx];
                if (src_uplift < 0.0) src_uplift = 0.0;
            }
        }

        double cur_elev = elevation[idx];

        /* 上坡量（仅正高程差） */
        double uplift = (cur_elev < src_elev) ? (src_elev - cur_elev) : 0.0;

        /* 有效抬升 = 上风抬升衰减 + 当前步抬升 */
        uplift_eff[idx] = src_uplift * decay + uplift;

        /* 映射到雨影因子 */
        factors[idx] = _uplift_to_factor(uplift_eff[idx], min_factor);
    }

    free(entries);
    free(uplift_eff);

    /* 高斯模糊平滑（消除海陆边界和其他局部跳变） */
    _gaussian_blur_inplace(factors, w, h, 2.0);
}

/* 原地可分离高斯模糊（用于雨影因子后处理） */
static void _gaussian_blur_inplace(double *arr, int w, int h, double sigma) {
    int n = w * h;
    double *tmp = (double *)malloc((size_t)n * sizeof(double));
    if (!tmp) return;

    int radius = (int)(sigma * 3.0);
    if (radius < 1) radius = 1;

    /* 水平 pass */
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            double sum = 0.0, weight = 0.0;
            for (int dx = -radius; dx <= radius; dx++) {
                int sx = x + dx;
                if (sx < 0) sx = 0;
                if (sx >= w) sx = w - 1;
                double g = exp(-(double)(dx * dx) / (2.0 * sigma * sigma));
                sum += arr[y * w + sx] * g;
                weight += g;
            }
            tmp[y * w + x] = sum / weight;
        }
    }

    /* 垂直 pass */
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            double sum = 0.0, weight = 0.0;
            for (int dy = -radius; dy <= radius; dy++) {
                int sy = y + dy;
                if (sy < 0) sy = 0;
                if (sy >= h) sy = h - 1;
                double g = exp(-(double)(dy * dy) / (2.0 * sigma * sigma));
                sum += tmp[sy * w + x] * g;
                weight += g;
            }
            arr[y * w + x] = sum / weight;
        }
    }

    free(tmp);
}

void hydrology_rain_shadow_omnidirectional(
    const double *elevation, int w, int h,
    double primary_angle,
    double secondary_angle,
    double secondary_weight,
    double decay_length_km,
    double cell_size_km,
    double min_factor,
    double *factors)
{
    /* 多风向抬升累积雨影因子。
       primary_angle: 主风向（弧度）。
       secondary_angle: 次风向（弧度），仅当 secondary_weight > 0 时使用。
       secondary_weight: 次风权重 [0, 1]。
       decay_length_km: 抬升指数衰减距离 (km)，替代滑动窗口。
       因子输出范围：[min_factor, 1.0]。
    */
    _rain_shadow_single_dir(elevation, w, h, primary_angle,
                            decay_length_km, cell_size_km, min_factor,
                            factors);

    if (secondary_weight > 0.0 && secondary_weight < 1.0) {
        int n = w * h;
        double *tmp = (double *)malloc((size_t)n * sizeof(double));
        if (tmp) {
            _rain_shadow_single_dir(elevation, w, h, secondary_angle,
                                    decay_length_km, cell_size_km, min_factor,
                                    tmp);
            double pw = 1.0 - secondary_weight;
            for (int i = 0; i < n; i++) {
                factors[i] = pw * factors[i] + secondary_weight * tmp[i];
            }
            free(tmp);
        }
    }
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
    const double *dist_to_ocean,
    int w, int h,
    double gx, double gy,
    double continentality_k,
    double continentality_d0,
    double cell_size_km,
    double *temp_out, double *rain_out, int *climate_out)
{
    /* 单次 C 遍历替代 Python 600K 循环。
       温度 = 纬度梯度 + 微量摆动 - 海拔 × 直减率 - 大陆度修正
       降雨 = 降雨噪声 × 雨影因子
       气候 = classify()

       大陆度修正：距海越远 → 年均温越低（大陆性气候，冬季降温主导年均值）。
       饱和指数曲线：接近海岸修正 ≈0，远海内陆修正 → continentality_k。
       dist_to_ocean == NULL 时跳过大 clo 陆度修正（向后兼容）。
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

        /* 大陆度修正：距海距离 → 饱和指数衰减 */
        if (dist_to_ocean != NULL && continentality_k > 0.0) {
            double d_km = dist_to_ocean[i] * cell_size_km;
            double cont_factor = 1.0 - exp(-d_km / continentality_d0);
            temp -= continentality_k * cont_factor;
        }

        if (temp < -20.0) temp = -20.0;
        if (temp > 36.0) temp = 36.0;

        double rain_n = rain_raw[i];
        double rainfall = rainfall_from_noise_c(rain_n) * rain_shadow[i];

        temp_out[i] = temp;
        rain_out[i] = rainfall;
        climate_out[i] = classify_climate(temp, rainfall, elev);
    }
}
