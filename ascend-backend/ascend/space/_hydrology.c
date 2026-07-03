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
    /* 上滤 */
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
