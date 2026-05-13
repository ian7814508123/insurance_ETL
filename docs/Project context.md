## 專案概述
本專案是一個保險業 ETL (Extract, Transform, Load) 工具，旨在從保險條款、費率表等文件中提取結構化數據，特別是保費計算相關的定義與數值。

## 核心功能
1. **保費提取 (Premium Extraction)**: 使用 `premium_extractor.py` 從 PDF 或圖片中提取費率表。
2. **名詞定義管理 (Definition Management)**: 
    - **資料結構**: 包含 `type` (固定為名詞定義), `description` (摘要), `code`, `display_name`, `base_definition`, `level`, `classification` 等。
    - **分層機制**: 分為「基本層 (Base)」與「商品層 (Product)」。
    - **基本層**: 基於示範條款，存放於 `data/definitions/base.json`。
    - **商品層**: 基於商品特約，存放於 `data/definitions/products/{CODE}.json`。
    - **覆蓋邏輯**: 商品層定義在運行時會覆蓋同名的基本層定義，避免汙染。

## 技術棧
- **語言**: Python 3
- **AI 模型**: Google Gemini (使用 `google-genai` SDK)
- **主要依賴**: `pypdf`, `google-genai`

## 目錄結構規範
- `src/`: 主要邏輯程式碼。
    - `pipeline/`: 多階段 Agent 核心邏輯（包含 5 大 Agent）。
    - `run_pipeline.py`: 自動化 Pipeline 啟動入口。
    - `manager.py`: 名詞定義與理賠項目的管理中心。
- `tests/`: 單元測試與整合測試。
- `docs/`: 說明文件、架構圖與 API 文件。
- `data/`: 存放處理後的結構化數據。
- `product/`: 預設的 PDF/TXT 輸入目錄。

## 功能模組說明 (src/ 核心功能)

### 1. 多階段 Agent Pipeline (`src/pipeline/`)
本專案採用五階段 Agent 協作模式，以確保複雜保險條款的提取精準度：
- **Agent 1: Segmenter (定位員)**: 負責在長文件中精確定位相關條款或給付項目區塊。
- **Agent 2: Registry (註冊員)**: 將定位到的文本轉化為結構化的基本定義與邏輯框架。
- **Agent 3: Logic Parser (邏輯解析員)**: 深入解析條款中的數學邏輯、給付倍數與計算公式。
- **Agent 4: Param Builder (參數建構員)**: 根據解析出的邏輯，提取並正規化所有輸入參數（如年齡、職業等級）。
- **Agent 5: Lookup Modeler (查表模型建構員)**: 將參數與邏輯封裝成可供程式呼叫的查表模型或計算邏輯。

### 2. 管理與提取工具
- **`manager.py`**: 提供用於合併、推播與管理 `base_{definition/claim_items}.json` 與產品專屬的定義檔。
- **`extractor.py` / `definition_extractor.py`**: 針對特定任務（如名詞定義提取）的專用工具。

## Pipeline 啟動指南

### 環境準備
確保已安裝必要依賴：
```bash
pip install -r requirements.txt
```
*(註：若無 requirements.txt，請確保安裝 `pypdf`, `pymupdf`, `google-genai`)*

### 啟動指令
使用 `run_pipeline.py` 啟動自動化處理流程：

```bash
python src/run_pipeline.py --input-dir ./product --level PRODUCT
```

### 常用參數說明
- `--input-dir`: 指定輸入文件（PDF 或 TXT）的目錄，預設為 `./product`。
- `--output-dir`: 指定 JSON 結果的輸出路徑，預設為 `./data/claim_items/products`。
- `--level`: 設定知識層級，可選 `BASE` (通用) 或 `PRODUCT` (商品專屬)。

### 工作流程
1. 將待處理的 PDF 或 TXT 檔案放入 `product/` 目錄。
2. 執行啟動指令。
3. 程式會自動進行 PDF 前處理（含 OCR 修正）、多階段 Agent 處理。
4. 最終結果將以 JSON 格式儲存於 `data/claim_items/products/{檔案名}.json`。

## 當前任務
- 條款解析試做: 針對少量樣本 PDF/TXT 進行全流程測試。
- 效果檢視評估: 檢查 JSON 輸出是否符合預期的結構化規範，特別是名詞定義的覆蓋邏輯（商品層是否正確覆蓋基本層）。

## 下一個任務
1. 模組整合: 將 `extractor.py` 與 `premium_extractor.py` 併入多階段 Pipeline 中。
2. 中介資料層建置 (Data Context Bridge): 建立統一的記憶體內或暫存檔案作為資料中介,可將PipelineContext (目前僅使用dict、list 的本機記憶體建立)改使用 Redis。
    - 強制規範 Param Builder 與 Lookup Modeler 的輸入/輸出欄位名稱。
    - 預期效益: 防止因獨立指令碼導致的欄位命名不一致、避免上下文斷鏈，確保條款邏輯與費率表能精確映射。
