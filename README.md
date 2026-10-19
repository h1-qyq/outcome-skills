# 榆次一中 · 校媒二次传播监控

手机优先看板：**本期（2026秋·4×100）** 与 **往期（百米出圈）** 分栏，禁止混算。

## 国内怎么打开（重要）

`beacon.host` 在国内经常打不开，**不要再依赖它发群**。优先：

1. **发给群_监控快照.html**（推荐）— 单文件、数据已内嵌，微信发文件 / 本机双击即可离线打开  
2. **GitHub Pages** 或 **jsDelivr** 托管 `index.html` + `data.json`（国内可达性通常好于 beacon）  
3. 本机 `python -m http.server` 同源打开 `index.html`

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 在线看板（fetch `data.json`，手动刷新） |
| `data.json` | 条目数据（本期 40 + 往期 13） |
| `发给群_监控快照.html` | 数据内嵌单文件，`file://` 可开 |
| `monitor-offline.html` | 与快照同内容备份 |

## 看板能力

- 分段：**本期 / 往期 / 全部**；绿标本期、琥珀标往期  
- 排序：发布时间新→旧，再按 `weight`（人民日报/央视等优先）  
- KPI：本期数、往期数、重点账号、当前列表数  
- 仅手动刷新按钮（**无** `setInterval` 轮询）  
- 搜索标题/账号/平台；按平台筛选  

## 更新数据后重出快照

改完 `data.json` 后运行 `python3 build.py`，会重新生成 index.html 和两个快照文件（模板在 `src/template.html`），然后再发群。
