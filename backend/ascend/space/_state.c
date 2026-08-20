/* 地形状态统一演化内核 — 湿润/覆雪/结冰逐 tile 推演。

与 _hydrology.c 同款模式：纯数值逐格循环下沉 C，Python 层薄包装。
本内核同时服务两条链路（同公式防漂移）：
  * 结算（settle）：步长 = 1 游戏日，n_steps = 结算天数，
    每日天气来自解析场固定时刻采样（Python 侧算好传入）；
  * 运行期脉冲：步长 = 1/24 游戏日，n_steps = 1，天气取当前实时值。

统一公式（日标定，delta × dt 缩放步长）：
  delta = precip_mm × deposit[t]                       # 沉积（降水）
        + freeze[t] × max(0, freeze_below − temp)      # 冻结（冰/雪压实）
        − state × ( melt[t] × max(0, temp − melt_above)
                  + drain[t] × (1 + slope) )           # 温度衰减 + 排水

参数表按 terrain id 索引（n_states × 256，不适用组合系数全 0 →
delta 恒 0，状态天然空转，无需适用性分支）。
state 每步 clamp 到 [0, state_max[s]]。

确定性契约：无 -ffast-math、无 -march=native（见 _cext.py），
同 seed 世界跨机器结果一致。
*/

#include <stdint.h>
#include <math.h>

/* 与 state_defs.py 的 StateParams.deposit/drain/melt/freeze 系数对应。
   states: n_states 行 × n 列的 uint8 数组（每行一块连续内存）。
   terrain: 每 tile 地形 id（uint16 存储，值 0-255，索引参数表）。
   slope: 每 tile 坡度（float32，排水修正 (1+slope)）。
   tile_cover: 每 tile 沉积倍率（NULL=1.0；运行期实体遮蔽传入，
    结算传 NULL——settle 时无实体）。
   step_precip: n_states × n_steps（行主序），每步每状态降水量 mm。
   step_temp: n_steps 每步均温 °C。
   deposit/drain/melt/freeze: n_states × 256 参数表（行主序）。
   freeze_below/melt_above/state_max: n_states。
   freeze_below <= -9000 表示该状态无冻结项。
 */
void state_evolve(
    uint8_t **states,
    const uint16_t *terrain,
    const float *slope,
    const double *tile_cover,
    int n,
    int n_states,
    int n_steps,
    const double *step_precip,
    const double *step_temp,
    double dt,
    const double *deposit,
    const double *drain,
    const double *melt,
    const double *freeze,
    const double *freeze_below,
    const double *melt_above,
    const double *state_max)
{
    int s, k, i;
    for (s = 0; s < n_states; s++) {
        uint8_t *arr = states[s];
        const double *dep_p = deposit + s * 256;
        const double *drn_p = drain + s * 256;
        const double *mlt_p = melt + s * 256;
        const double *frz_p = freeze + s * 256;
        const double fb = freeze_below[s];
        const double ma = melt_above[s];
        const double hi = state_max[s];
        if (hi <= 0.0) continue; /* 未启用状态 */
        for (k = 0; k < n_steps; k++) {
            const double prec = step_precip[s * n_steps + k];
            const double temp = step_temp[k];
            const double melt_k = temp > ma ? (temp - ma) : 0.0;
            const double freez_k =
                (fb > -9000.0 && temp < fb) ? (fb - temp) : 0.0;
            /* 全零日（无降水无冻结）：仅衰减非零 tile，其余跳过 */
            const int decay_only = (prec == 0.0 && freez_k == 0.0);
            for (i = 0; i < n; i++) {
                double v = (double)arr[i];
                if (v == 0.0 && decay_only) continue;
                int t = (int)terrain[i];
                double dep = prec * dep_p[t]
                           * (tile_cover ? tile_cover[i] : 1.0);
                double delta = dep
                             + freez_k * frz_p[t]
                             - v * (mlt_p[t] * melt_k
                                    + drn_p[t] * (1.0 + (double)slope[i]));
                v += delta * dt;
                if (v < 0.0) v = 0.0;
                else if (v > hi) v = hi;
                arr[i] = (uint8_t)(v + 0.5);
            }
        }
    }
}