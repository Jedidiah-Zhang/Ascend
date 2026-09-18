# 历史探针（归档）

这些脚本是早期研究探针的**历史快照**，按新理论框架重新解释后的证据状态见
[世界基座 06：探针结果](../../docs/研究理论/世界基座/06-探针结果.md)：

- 数值保留，不因理论重构删除；
- 旧判据中的过强结论、错误预算口径与事后调参**不得作为确认性证据**；
- **不作为 CI 门禁**：脚本打印 PASS/FAIL 供人工阅读，无退出码语义。

归档原因（issue #50）：入口路径失修（`parents[...]` 指向错误）且打印 FAIL
不返回非零退出码；与其在历史脚本上维护门禁语义，不如移出研究入口、
保留为可运行的历史记录。入口路径已随归档修正：

```bash
.venv/bin/python research/historical/engine_weather.py --fast   # E1–E3
.venv/bin/python research/historical/engine_e4_5.py  --fast     # E4/E5
.venv/bin/python research/historical/engine_e6.py    --fast     # E6
```

`research/toy_scm.py`（S1–S6，不依赖后端）未归档，仍在 `research/` 下。
