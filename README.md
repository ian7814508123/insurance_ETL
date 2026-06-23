# 專案名稱: 保險業 ETL 解析工具 (Insurance Premium & Clause ETL)

## 📖 專案願景與目標 (Project Vision)
本專案致力於打造一套專為保險業設計的 ETL (Extract, Transform, Load) 自動化工具，解決保險條款與費率表高度非結構化、難以直接用於數位系統的痛點。

我們不僅是進行單純的文字 OCR，更是將**「法律條文」轉化為「可計算、可試算」的 JSON 結構模型**，以支援未來前端的動態渲染、精準需求比對與自動化保費試算。

## 🗺️ 模組開發藍圖 (Development Roadmap)
根據產品發展規劃，本專案分為三大核心模組：

### 模組 A：費率自動化 (Rate Automation)
- **費率 Schema 與維度設計**：包含計算邏輯 (Lookup/Multiply) 與組合保費結構。
- **費率解析測試 (PoC)**：支援不同保險公司格式的費率表 AI 提取，並確保高信心度。

### 模組 B：條款自動化 (Clause Automation)
- **語意對齊與同義詞庫**：前處理掃描條款建立 Standard Tag 與 Synonym Map，避免 AI 在解析時自創名詞，確保前後端使用同一套資料標準。
- **給付項目公式化**：將條文閱讀用文字轉化為試算用的參數（例如：將「限額1萬」定義為 `limit: 10000`）。
- **Agent 解析**：訓練 AI 定位條款區塊並產出結構化的 Benefit JSON。

### 模組 C：管理後台與展示 (Admin & Display)
- **商品資訊編輯器**：提供費率預覽、給付項目的工程師人工校對功能。
- **動態渲染引擎**：讓前端能直接讀取結構化 JSON，渲染出保險試算結果與保障內容。

*(註：本專案亦探索了整合 NoteBookLM 作為商品知識庫與同步管線的應用)*

---

## ✨ 核心系統架構 (Core Architecture)
為了實現上述的**條款自動化**，系統實作了多階段 Agent 協作解析 (Multi-Agent Pipeline) 流程：
1. **Segmenter (定位員)**：精確定位長文件中的條款與給付區塊。
2. **Registry (註冊員)**：將文本轉為結構化的基本定義與邏輯框架。
3. **Logic Parser (邏輯解析員)**：解析條款中的數學邏輯、給付倍數與公式。
4. **Param Builder (參數建構員)**：正規化輸入參數（如年齡、職業等級）。
5. **Lookup Modeler (查表模型建構員)**：封裝參數與邏輯為可呼叫的查表模型。

## 🛠️ 技術棧 (Tech Stack)
- **程式語言**：Python 3
- **AI 引擎**：Google Gemini (透過 `google-genai` SDK)
- **核心套件**：`pypdf`, `pymupdf` (PDF 處理), `google-genai` (LLM 解析)

## 📁 目錄結構規範
```text
保費試算/
├── src/                # 主要邏輯程式碼
│   ├── pipeline/       # 多階段 Agent 核心邏輯
│   ├── run_pipeline.py # 自動化 Pipeline 啟動入口
│   └── manager.py      # 名詞定義與理賠項目管理中心
├── tests/              # 單元測試與整合測試
├── docs/               # 說明文件與架構圖（包含模組藍圖等）
├── data/               # 處理後的結構化數據輸出目錄
├── product/            # 預設的 PDF/TXT 輸入目錄
└── contexts/           # 專案上下文與核心知識庫
```

## 🚀 快速啟動 (Quick Start)

### 1. 環境準備
請確保您的環境中已安裝 Python 3，並安裝必要的依賴套件：
```bash
pip install -r requirements.txt
```

### 2. 執行 Pipeline
將待處理的保險條款 PDF 或 TXT 檔案放入 `product/` 目錄中，並執行以下指令啟動自動化流程：

```bash
python src/run_pipeline.py --input-dir ./product --level PRODUCT
```

**常用參數說明：**
- `--input-dir`: 指定輸入目錄（預設：`./product`）
- `--output-dir`: 指定 JSON 結果輸出路徑（預設：`./data/claim_items/products`）
- `--level`: 設定知識層級，可選 `BASE` (通用) 或 `PRODUCT` (商品專屬)
