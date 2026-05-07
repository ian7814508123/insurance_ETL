import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

# 將當前工作目錄加入路徑，以便導入根目錄的 config
sys.path.append(os.getcwd())

from google import genai
from google.genai import types
import config

import pymupdf


class DefinitionExtractor:
    """名詞定義提取器，負責從條款文本或 PDF 中整理結構化 JSON。"""

    def __init__(
        self,
        api_key: str = config.GEMINI_API_KEY,
        model_name: str = config.DEFAULT_MODEL,
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "type",
                    "description",
                    "code",
                    "display_name",
                    "base_definition",
                    "level",
                    "classification",
                ],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["名詞定義"],
                        "description": "固定為 '名詞定義'",
                    },
                    "description": {
                        "type": "string",
                        "description": "名詞的簡短描述或摘要",
                    },
                    "code": {
                        "type": "string",
                        "description": "名詞唯一代號 (大寫蛇形命名)，例如 POLICY_HOLDER",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "條款書上顯示的名詞名稱，例如 要保人",
                    },
                    "base_definition": {
                        "type": "string",
                        "description": "名詞的原始定義內容",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["BASE", "PRODUCT", "CATEGORY"],
                        "description": "定義層級：BASE (基本層), PRODUCT (商品層),CATEGORY(險種級別層)",
                    },
                    "classification": {
                        "type": "string",
                        "enum": [
                            "NEW_GENERAL",
                            "PRODUCT_SPECIFIC",
                            "OVERRIDE",
                            "EXISTING_MATCH",
                        ],
                        "description": "分類：NEW_GENERAL(通用漏網), PRODUCT_SPECIFIC(商品特約), OVERRIDE(修改基本定義), EXISTING_MATCH(與基本層一致)",
                    },
                    "origin_product": {
                        "type": "string",
                        "description": "若是商品層，紀錄所屬商品代碼",
                    },
                    "parameter": {
                        "type": "object",
                        "description": "定義中抽離出的參數化條件",
                    },
                    "synonym_map": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "同義詞列表",
                    },
                },
            },
        }

    def extract_images_from_pdf(self, pdf_path: str, max_pages: int = 10) -> List[Any]:
        """使用 PyMuPDF 從 PDF 中提取前幾頁並轉換為圖片 Parts，避免 Unicode 損壞。"""
        doc = pymupdf.open(pdf_path)
        image_parts = []
        for i in range(min(max_pages, len(doc))):
            page = doc[i]
            # 提高 DPI 以確保文字清晰 (3x zoom = 216 DPI)
            zoom = 3
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpg")
            image_parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
            )
        doc.close()
        return image_parts

    def load_definitions(self, file_path: str) -> List[Dict[str, Any]]:
        """讀取現有的定義 JSON 檔案。"""
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # 過濾掉缺少 code 的無效項目 (例如錯誤訊息物件)
                        return [d for d in data if isinstance(d, dict) and "code" in d]
                    return []
            except (json.JSONDecodeError, IOError):
                print(f"Warning: Failed to read {file_path}, starting fresh.")
        return []

    def save_definitions(self, definitions: List[Dict[str, Any]], file_path: str):
        """儲存定義到 JSON 檔案。"""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(definitions, f, ensure_ascii=False, indent=2)

    def extract_definitions(
        self,
        content: Any,  # 可以是 str (文字) 或 List[types.Part] (圖片)
        context_definitions: List[Dict[str, Any]],
        level: str = "BASE",
        product_code: str = None,
    ) -> List[Dict[str, Any]]:
        """呼叫 LLM 從文本或圖片中提取定義，並進行分類判定。"""
        # 提供現有的名詞清單作為參考
        context_summary = [
            {"code": d["code"], "display_name": d.get("display_name", d["code"])}
            for d in context_definitions
            if isinstance(d, dict) and "code" in d
        ]

        prompt = f"""
        作為保險法務與精算專家，請從以下保險條款文本或圖片中提取「名詞定義」。
        
        當前提取層級：{level}
        {f"所屬商品名稱/代碼：{product_code}" if product_code else ""}
        
        任務要求：
        1. 識別文本中的核心名詞定義。
           - 通常在條款的「名詞定義」章節，或是章節中以粗體標示的名詞。
           - 請優先尋找以下三種特定模式並提取其中的『名詞』與其後續的「定義內容」：
            * 模式 A (引號係指型)：出現「本契約所稱『(名詞)』係指(定義)。」 -> 必須精確抓取引號內的文字。
            * 模式 B (清單冒號型)：出現「(編號)、(名詞)：指(定義)。」或「(名詞)：(定義)。」
            * 模式 C (術語定義型)：出現「所謂『(名詞)』，係指......」。
            **注意：** 即便句中包含多層引號，你的任務是提取「被定義的主體」。若發現「係指」二字，其前方被引號包圍的字串即為 `display_name`。
        2. **欄位規範**：
           - `type`: 固定為「名詞定義」。
           - `display_name`: 提取出的名詞（去除所有引號）。
           - `description`: 簡短白話摘要（不超過 30 字）。
           - `base_definition`: 原始條文文本（完整保留，從名詞後的第一個字開始直到句號結束）。
        3. **分類判定 (Classification)**：
           請參考下表進行 `classification` 標註：
           - 比對基本層已知：{context_summary}
           - 若名詞不在基本層，但屬於通用保險法律/精算術語（例如：要保人、受益人、寬限期間） -> `NEW_GENERAL`
           - 若名詞包含特定的商品名稱、特定計畫名稱或僅限此保單的特殊規則 -> `PRODUCT_SPECIFIC`
           - 若名詞已在基本層，但此商品的定義與基本層有顯著條文差異 -> `OVERRIDE`
           - 若名詞已在基本層且定義大致一致 -> `EXISTING_MATCH`
        
        待處理內容：
        ---
        """

        # 組合 Prompt 與 內容
        contents = [prompt]
        if isinstance(content, str):
            contents.append(content)
        else:
            # content 是 List[types.Part] (圖片)
            contents.extend(content)

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=self.schema,
                temperature=0.1,
            ),
        )

        try:
            results = json.loads(response.text)
            for item in results:
                item["level"] = level
                if product_code:
                    item["origin_product"] = product_code
            return results
        except json.JSONDecodeError:
            print(" LLM returned invalid JSON.")
            return []

    def merge_definitions(
        self, base: List[Dict[str, Any]], new_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """合併新舊定義。如果 code 已存在，則更新其內容以符合最新 Schema。"""
        # 建立現有定義的映射，確保每個項目都有 code
        merged = {d["code"]: d for d in base if isinstance(d, dict) and "code" in d}

        for item in new_items:
            code = item["code"]
            if code in merged:
                # 更新基本欄位以符合新 Schema
                merged[code]["type"] = item.get("type", "名詞定義")
                if "description" in item:
                    merged[code]["description"] = item["description"]

                # 合併同義詞與參數
                existing_synonyms = set(merged[code].get("synonym_map", []))
                new_synonyms = set(item.get("synonym_map", []))
                merged[code]["synonym_map"] = list(
                    existing_synonyms.union(new_synonyms)
                )

                merged[code].setdefault("parameter", {}).update(
                    item.get("parameter", {})
                )

                # 更新分類標籤 (如果新提取的更準確)
                merged[code]["classification"] = item.get(
                    "classification", merged[code].get("classification")
                )

                # 更新定義內容 (保留較長或較新的)
                if len(item.get("base_definition", "")) > len(
                    merged[code].get("base_definition", "")
                ):
                    merged[code]["base_definition"] = item["base_definition"]
            else:
                merged[code] = item

        return list(merged.values())


def process_directory(input_dir: str, output_base_dir: str, level: str = "BASE"):
    """處理整個目錄下的條款檔案。"""
    extractor = DefinitionExtractor()
    base_file = os.path.join(output_base_dir, "base.json")
    base_defs = extractor.load_definitions(base_file)

    input_path = Path(input_dir)
    # 支援 .txt 與 .pdf
    files = sorted(
        [
            f
            for f in input_path.iterdir()
            if f.suffix.lower() in [".txt", ".pdf"] and f.name != "說明.txt"
        ]
    )

    print(f"Found {len(files)} files to process for {level} layer.")

    for i, file_path in enumerate(files):
        print(f"[{i + 1}/{len(files)}] Processing: {file_path.name}...")

        try:
            if file_path.suffix.lower() == ".pdf":
                content = extractor.extract_images_from_pdf(str(file_path))
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

            if level == "BASE":
                output_file = base_file
                current_defs = base_defs
                product_code = None
            else:
                # 商品層：每個商品一個檔案
                product_code = file_path.stem
                output_file = os.path.join(
                    output_base_dir, "products", f"{product_code}.json"
                )
                current_defs = extractor.load_definitions(output_file)

            new_defs = extractor.extract_definitions(
                content, base_defs, level=level, product_code=product_code
            )

            # 過濾掉 EXISTING_MATCH (如果不想在商品層存重複的東西，但保留它們有助於 Context 完整)
            # 這裡我們選擇保留，但使用者可以根據需求過濾

            current_defs = extractor.merge_definitions(current_defs, new_defs)
            extractor.save_definitions(current_defs, output_file)
            print(
                f"Successfully updated {len(new_defs)} definitions to {os.path.basename(output_file)}."
            )

            if i < len(files) - 1:
                time.sleep(2)

        except Exception as e:
            print(f"Error processing {file_path.name}: {str(e)}")


if __name__ == "__main__":
    import sys

    # 使用方式：python definition_extractor.py [mode: BASE|PRODUCT]
    mode = sys.argv[1] if len(sys.argv) > 1 else "BASE"

    OUTPUT_BASE_DIR = r"c:\Users\User\Downloads\保費試算\data\definitions"

    if mode == "BASE":
        INPUT_DIR = r"c:\Users\User\Downloads\保費試算\base_definition"
        process_directory(INPUT_DIR, OUTPUT_BASE_DIR, level="BASE")
    else:
        INPUT_DIR = r"c:\Users\User\Downloads\保費試算\product"
        process_directory(INPUT_DIR, OUTPUT_BASE_DIR, level="PRODUCT")
