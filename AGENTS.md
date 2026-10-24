# AGENTS.md - AI Agent Integration & Usage Guide

本指南專供所有接入本系統的 AI Agent（包括本機與跨主機遠端 Agent）參考。

## 1. Agent 核心能力與定位
當你需要查找儲存於公司 NFS 叢集（如 `/mnt/...` 下數十萬個樣品資料夾）中的生物資訊資料（Raw FASTQ、BAM、VCF、CNV、QC Report）時，**絕不要自行在 Bash 執行 `find /mnt`**，這會導致伺服器 IO 飽和與逾時。

**一律呼叫此 MCP 工具或執行 `biofile-cli`。**

---

## 2. MCP Tools 快速指南

| Tool Name | 何時使用 | 關鍵參數範例 |
| :--- | :--- | :--- |
| `find_sample_files` | 使用者提及特定檢體編號/條碼時 | `barcode="AANB01_513"`, `ext="bam"` |
| `search_files` | 查找特定檔名、日期、大檔案或萬用字元 | `query="*R1*.fastq.gz"`, `min_size_mb=100` |
| `list_folder` | 想要看某個路徑下有哪些子資料夾與檔案時 | `folder_path="/mnt/RD_Result"` |
| `read_snippet` | 遠端 Agent 沒有掛載該 NFS，但需要讀取報告開頭/日誌時 | `path="/mnt/.../report.txt"`, `lines=30` |
| `get_index_status` | 評估特定 NFS 掛載點是否有被納入索引時 | 無參數 |

---

## 3. Agent 常用 Bash 呼叫模式
如果你具備 Bash 工具且需要將結果整合進腳本：
```bash
# 秒級取得檔案絕對路徑清單
biofile-cli search "<樣品名>" --ext bam -1

# 取得 JSON 格式並交由 jq 解析
biofile-cli search "<樣品名>" --json
```

---

## 4. 自動化測試驗證指令 (Agent Self-Test)
Agent 接入後可自行執行此指令驗證服務狀態：
```bash
biofile-cli status
biofile-cli search "AANB01*" --limit 3
```
若回傳包含 `Found 3 matches in X ms` 即代表連線與索引工作正常。
