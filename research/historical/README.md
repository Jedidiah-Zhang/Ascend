# 历史探针（快照）

这些脚本是早期研究探针的**快照**，数值与运行方式保留：

- 数值保留，结论只作历史记录，**不作为确认性证据**；
- 打印 PASS/FAIL 供人工阅读，无退出码语义；
- **不参与 CI**；
- 依赖未固化（numpy / scipy / sklearn / statsmodels 等研究侧依赖不在
  `requirements.txt` 内）。

运行：

```bash
.venv/bin/python research/historical/engine_weather.py --fast   # E1–E3
.venv/bin/python research/historical/engine_e4_5.py  --fast     # E4/E5
.venv/bin/python research/historical/engine_e6.py    --fast     # E6
```

`research/toy_scm.py`（S1–S6，不依赖后端）在 `research/` 下。
