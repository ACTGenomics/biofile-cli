# Centralized NFS File Search MCP Server 實作計畫

本計畫旨在將 `biofile-manager` 既有的巨量 NFS 索引資料庫（SQLite + FTS5），封裝為標準 **MCP (Model Context Protocol) Server**，供本機及其他主機上的 AI Agent（如 Claude Code、Cursor、自建生信 Agent 等）遠端秒級查詢數十至數百 TB 的檔案、定序樣本與分析結果。

---

## 1. 架構全景 (Architecture Overview)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        主伺服器 (Index Host)                            │
│                                                                        │
│   [ biofile-manager 既有排程 ]                                          │
│   (負責定期掃描 NFS、維護資料庫)                                           │
│               │                                                        │
│               ▼                                                        │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │ index.db (SQLite WAL Mode)                                   │     │
│   │ 包含: files 表、files_fts 全文索引、dirs 目錄表、scans 紀錄      │     │
│   └──────────────────────┬───────────────────────────────────────┘     │
│                          │ 唯讀連接 (mode=ro, 永不鎖定)                  │
│                          ▼                                             │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │ NFS Search MCP Server (FastMCP / SSE / Stdio)                │     │
│   │ 監聽 HTTP/SSE 埠號 (例如 :8090) 或透過 SSH Stdio 調用         │     │
│   └──────────────────────▲───────────────────────────────────────┘     │
└──────────────────────────┼─────────────────────────────────────────────┘
                           │ 網路請求 (MCP JSON-RPC / SSE)
            ┌──────────────┴──────────────┐
            ▼                             ▼
┌────────────────────────┐   ┌────────────────────────┐
│ 遠端主機 A 的 Agent     │   │ 遠端主機 B 的 Agent     │
│ (完全不需裝 DB / 掃描) │   │ (完全不需裝 DB / 掃描) │
└────────────────────────┘   └────────────────────────┘
```

### 核心特性
- **一處索引，全網共用**：遠端 Client 端完全零負擔，無需重新掃描，無需在遠端裝 SQLite。
- **絕對零衝突與高效能**：以 SQLite `mode=ro`（唯讀模式）連線，搭配 SQLite WAL 讀寫分離，背景爬蟲全量/增量掃描時，前端查詢完全不阻塞。
- **毫秒級響應**：利用 FTS5 全文索引與已建置的 B-Tree 索引，單次查詢一般在 5~30 毫秒內完成。

---

## 2. 提供給 Client Agent 的工具清單 (MCP Tools)

為生信與檔案查找量身打造 5 個核心工具：

| 工具名稱 (Tool) | 參數說明 | 用途與效果 |
| :--- | :--- | :--- |
| **`search_files`** | `query`: 關鍵字、萬用字元（如 `*R1*.fastq.gz`）<br>`roots`: 限定特定掛載點/Share<br>`ext`: 指定副檔名（如 `bam`, `vcf.gz`）<br>`barcode`: 樣品條碼（如 `AANB01_123`）<br>`date_after` / `date_before`: 日期範圍<br>`min_size_mb` / `max_size_mb`: 檔案大小範圍<br>`kind`: `file` 或 `dir`<br>`limit` (預設 50, 上限 500) | **通用型秒級檢索**。<br>底層對齊 `index_query.go` 規則，整合 FTS5 與 SQL 複合索引。 |
| **`find_sample_files`** | `barcode`: 樣品條碼（必填，例如 `AANB01`）<br>`ext`: 篩選副檔名（選填）<br>`limit`: 最大回傳筆數 | **生信專用捷徑**。<br>一次找出該檢體的所有分析輸出（Raw FASTQ、BAM、VCF、QC report）。 |
| **`list_folder`** | `parent_path`: 目錄絕對路徑<br>`limit`: 筆數限制 | **虛擬目錄瀏覽**。<br>不直接存取 NFS，直接從 DB 的 `parent` 欄位列出下一層子目錄與檔案，速度極快且不給 NAS 壓力。 |
| **`read_snippet`** | `path`: 檔案路徑<br>`lines`: 行數 (預設 30)<br>`mode`: `head` 或 `tail` | **遠端預覽文字檔**。<br>若遠端 Client 沒有掛載 NFS，可透過此工具由 Index 主機代為讀取報告、日誌或 QC 結果摘要。 |
| **`get_index_status`** | 無 | **索引健康度與總覽**。<br>回傳目前收錄的 Shares 清單、檔案總數、最後掃描時間與狀態，讓 Agent 評估搜尋覆蓋範圍。 |

---

## 3. 技術選型建議 (Tech Stack)

推薦使用 **Python (FastMCP)** 或 **Go**，兩種方案各有優勢：

### 推薦方案：Python + `FastMCP` (mcp SDK)
- **優勢**：
  - `FastMCP` 是 Anthropic / Model Context Protocol 官方推薦的輕量標準框架。
  - 原生自帶 **SSE (Server-Sent Events)** 伺服器與 stdio 雙模式，只需一個指令即可啟用遠端連線。
  - 易於整合 `sqlite3`、擴充權限控管與自訂格式輸出。
- **依賴**：`python3`, `mcp[cli]`, `pydantic`。

### 備選方案：Go (延伸 biofile-manager)
- **優勢**：
  - 可以直接引用 `biofile-manager/backend/services` 中的 `index_query.go`，查詢邏輯 100% 保持一致。
  - 編譯成單一二進位執行檔（Zero dependency）。
- **缺點**：
  - Go 的 MCP SDK 在遠端 SSE 支援上不如 Python FastMCP 生態成熟。

> **建議採用 Python FastMCP**：開發迅速、程式碼直觀、維護最彈性。

---

## 4. 遠端 Agent 連線模式 (Connection Modes)

### 模式 A：HTTP / SSE 遠端模式（最推薦）
在 Index 伺服器上啟動 MCP SSE Server，監聽內網 IP（例如 `0.0.0.0:8090`）：

**遠端 Client 配置範例 (`mcp_config.json` 或 Claude / Cursor 設定)：**
```json
{
  "mcpServers": {
    "nfs_search": {
      "url": "http://192.168.5.X:8090/sse"
    }
  }
}
```

### 模式 B：SSH Stdio 模式（免開 HTTP 埠號）
若內網環境有防火牆限制，遠端 Agent 可透過 SSH Key 直接調用本機腳本：

```json
{
  "mcpServers": {
    "nfs_search": {
      "command": "ssh",
      "args": ["-T", "cgh2@192.168.5.X", "python3", "/mnt/home/cgh2/pi-projects/biofile-manager/mcp_server/server.py"]
    }
  }
}
```

---

## 5. 實施步驟與驗收階段 (Phases & Milestones)

### Phase 1: 環境與路徑鎖定 (已由系統自動驗證完畢 ✅)
- **Database 確切路徑**：`/srv/biofile-manager/data/index.db` (包含 12,472,692 筆檔案、1,164,148 個目錄)。
- **連線與權限**：以 SQLite 唯讀模式 `file:/srv/biofile-manager/data/index.db?mode=ro` 存取，經實測在 1240 萬筆資料中，FTS5 檢索僅需 ~400ms，單一條件或指定目錄 < 50ms。
- **Python 執行環境**：`/mnt/home/cgh2/miniconda3/bin/python` (Python 3.12.9)。
- **專案安裝目錄**：建議建立於 `/mnt/home/cgh2/pi-projects/biofile-manager/mcp-server` 或獨立目錄 `/srv/biofile-manager-mcp`。

### Phase 2: 安裝依賴與編寫核心 MCP Server
- 安裝 `mcp` (`pip install "mcp[cli]"` 或輕量 `fastmcp` / `starlette` / `uvicorn`)。
- 實作唯讀 SQLite 查詢模組（對齊 `index_query.go`，支援 FTS5 全文比對、萬用字元 Glob LIKE 轉換、生信 Barcode/Ext 精準過濾）。
- 實作 5 個 MCP Tools：`search_files`, `find_sample_files`, `list_folder`, `read_snippet`, `get_index_status`。

### Phase 3: 本地驗證與端點測試
- 測試 FTS5 與萬用字元查詢、Barcode 篩選與大小限制。
- 測試目錄展開（`list_folder`）與文字摘要讀取（`read_snippet`）。
- 模擬並發查詢，確保完全不阻塞後台 `biofile-manager` 的掃描更新。

### Phase 4: Systemd 守護進程與網路暴露 (SSE 模式)
- 建立 systemd 服務：`biofile-mcp.service`，監聽內網連接埠（例如 `0.0.0.0:8090`）。
- 設定自動重啟與日誌輪替。

### Phase 5: Client 端配置與跨主機呼叫實測
- 產出 Claude Code (`~/.claude/claude_desktop_config.json` 或 `.mcp.json`)、Cursor、自建 Agent 的設定檔。
- 在遠端主機上調用測試，確認遠端 Agent 可秒級檢索 NFS 檔案。

---

## 6. 請確認與反饋項目 (Review Questions)

1. **技術選型**：是否同意採用 **Python + FastMCP** 來開發？
2. **連線方式**：預期主要以 **HTTP/SSE（指定內網 Port）** 還是 **SSH Stdio** 供遠端 Agent 調用？
3. **Database 路徑**：目前生產環境中的 `index.db` 檔案完整路徑為何？

