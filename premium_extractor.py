from google import genai
from google.genai import types
import json
import time
import os
import tempfile
import shutil
from typing import Dict, Any, List
import config


client = genai.Client(api_key="GEMINI_API_KEY")


def safe_json_loads(text: str, context: str = "未知階段") -> Any:
    """安全的 JSON 解析，失敗時儲存產生的文本以便除錯"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        debug_path = os.path.join(
            os.getcwd(), f"failed_llm_response_{int(time.time())}.json"
        )
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n🚨 [JSON 解析錯誤] 發生於 {context}")
        print(f"錯誤詳情: {e}")
        print(f"📄 已將 LLM 原始回傳文本儲存至: {debug_path}\n")
        raise RuntimeError(
            f"LLM 產生了無效的 JSON ({context})，這通常是因為文件過長、超過 Token 限制或模型幻覺。"
        ) from e


class QualityEvaluator:
    """Stage 0: 文件品質評核模組"""

    def __init__(self, client, model_name="gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def evaluate(self, file) -> Dict[str, Any]:
        prompt = """
        作為一個文件分析專家，請評核此保險費率文件（圖片或 PDF）的品質。
        請針對以下維度評分 (1-5)：
        1. 清晰度 (Clarity): 文字是否易於辨識，無模糊。
        2. 完整性 (Completeness): 表格是否完整，無切邊或遮擋。
        3. 排版複雜度 (Complexity): 格式是否混亂、對齊是否正常。

        請以 JSON 格式輸出：
        {
            "overall_score": number, (1-5)
            "dimensions": {
                "clarity": number,
                "completeness": number,
                "complexity": number
            },
            "is_low_quality": boolean, (overall_score < 3 則為 true)
            "reason": "說明原因"
        }
        """
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.1
            ),
        )
        return safe_json_loads(response.text, "Stage 0: 文件品質評核")


class RateExtractor:
    """Stage 1: 純資料解析模組 (使用完整原始 Prompt)"""

    def __init__(self, client, model_name="gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def get_schema(self) -> Dict[str, Any]:
        """完整原始 Schema (不含信心欄位)"""
        return {
            "type": "object",
            "required": ["metadata", "rate_table"],
            "properties": {
                "metadata": {
                    "type": "object",
                    "properties": {
                        "product_name": {
                            "type": "string",
                            "description": "產品完整中文名稱",
                        },
                        "product_family": {
                            "type": "string",
                            "enum": ["LIFE", "MEDICAL", "ACCIDENT", "CANCER"],
                            "description": "商品類型描述，引導 AI 關注特定維度",
                        },
                        "semantic_context": {
                            "type": "object",
                            "description": "輔助標籤，不限制邏輯判斷",
                            "properties": {
                                "primary_risk": {
                                    "type": "string",
                                    "enum": ["DEATH", "MEDICAL", "ACCIDENT", "SAVINGS"],
                                },
                                "target_audience": {
                                    "type": "string",
                                    "description": "如：一般、婦女、高齡",
                                },
                            },
                        },
                        "product_code": {
                            "type": "string",
                            "description": "商品代碼/報備編號",
                        },
                        "calculation_logic": {
                            "type": "string",
                            "enum": ["MULTIPLY", "LOOKUP"],
                            "description": "計算模式：MULTIPLY(保額型線性計算), LOOKUP(計畫型直接查表)",
                        },
                        "pricing_basis": {
                            "type": "string",
                            "enum": ["UNIT_SA", "UNIT_DAILY", "PLAN"],
                            "description": "定價基礎：UNIT_SA(保額), UNIT_DAILY(日額), PLAN(計畫別)",
                        },
                        "currency": {
                            "type": "string",
                            "description": "ISO 4217 幣別代碼，如 TWD, USD, CNY",
                        },
                        "unit_config": {
                            "type": "object",
                            "properties": {
                                "unit_value": {
                                    "type": "integer",
                                    "description": "基數數值，如 10000",
                                },
                                "unit_text": {
                                    "type": "string",
                                    "description": "原文描述，如 '每萬元保額'、'每千元日額'",
                                },
                            },
                        },
                        "frequency_factors": {
                            "type": "object",
                            "description": "繳費方式換算係數",
                            "properties": {
                                "ANNUAL": {"type": "number", "default": 1.0},
                                "SEMI_ANNUAL": {
                                    "type": "number",
                                    "description": "半年繳係數，如 0.52",
                                },
                                "QUARTERLY": {
                                    "type": "number",
                                    "description": "季繳係數，如 0.262",
                                },
                                "MONTHLY": {
                                    "type": "number",
                                    "description": "月繳係數，如 0.088",
                                },
                            },
                        },
                        "benefit_period": {
                            "type": "object",
                            "required": ["type", "value"],
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["YEAR", "AGE", "LIFE"],
                                },
                                "value": {
                                    "type": "integer",
                                    "description": "壽險家族若 type 為 LIFE，此值填 0 或 條款定義之最高年齡",
                                },
                            },
                        },
                        "max_insurance_age": {
                            "type": "integer",
                            "description": "最高可投保年齡 (Metadata勾稽用)",
                        },
                        "occupation_limit": {
                            "type": "integer",
                            "description": "最高承保職業等級，通常為 1-6,若無則填 0",
                        },
                        "limit_logic": {
                            "type": "object",
                            "properties": {
                                "min_unit": {
                                    "type": "number",
                                    "default": 0,
                                    "description": "最低投保金額/單位",
                                },
                                "max_unit": {
                                    "type": "number",
                                    "default": 0,
                                    "description": "最高額度，若無限制填 0",
                                },
                                "step_unit": {
                                    "type": "number",
                                    "default": 0,
                                    "description": "跳檔級距，若固定額度不可調則填 0",
                                },
                                "limit_text": {
                                    "type": "string",
                                    "description": "原始投保規則描述",
                                },
                            },
                        },
                        "version": {
                            "type": "string",
                            "description": "費率表版號或生效日期",
                        },
                    },
                    "required": [
                        "product_name",
                        "calculation_logic",
                        "pricing_basis",
                        "currency",
                    ],
                },
                "rate_table": {
                    "type": "array",
                    "description": "核心費率矩陣",
                    "items": {
                        "type": "object",
                        "required": [
                            "gender",
                            "occupation_level",
                            "social_insurance",
                            "contract_year_type",
                            "premium_period",
                            "variant_tags",
                            "discount_config",
                            "sum_assured_tier",
                        ],
                        "properties": {
                            "gender": {"type": "string", "enum": ["M", "F", "BOTH"]},
                            "age_start": {"type": "integer"},
                            "age_end": {"type": "integer"},
                            "occupation_level": {
                                "type": "integer",
                                "description": "適用職業等級，若不分則填 0",
                            },
                            "social_insurance": {
                                "type": "string",
                                "enum": ["Y", "N", "BOTH"],
                                "description": "Y:有社保, N:無社保, BOTH:不分",
                                "default": "BOTH",
                            },
                            "contract_year_type": {
                                "type": "string",
                                "enum": ["FIRST_YEAR", "RENEWAL", "BOTH"],
                                "description": "FIRST_YEAR:首次投保, RENEWAL:續保, BOTH:不分",
                                "default": "BOTH",
                            },
                            "premium_period": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["YEAR", "AGE", "SINGLE"],
                                        "description": "YEAR: 定期(如10年期); AGE: 至某歲(如至65歲); SINGLE: 一次繳清",
                                    },
                                    "value": {
                                        "type": "integer",
                                        "description": "繳費年期: 若為 YEAR 填年期數字; 若為 AGE 填歲數; 若為 SINGLE 填 0",
                                    },
                                },
                            },
                            "variant_tags": {
                                "type": "object",
                                "description": "存放如 '還本年齡' 或 '特定保障類型',無特定屬性設為null",
                                "required": ["return_age"],
                                "properties": {
                                    "return_age": {
                                        "type": "integer",
                                        "description": "還本年齡，如 55, 65。非還本型填0",
                                        "default": 0,
                                    },
                                    "payout_type": {
                                        "type": "string",
                                        "default": "STANDARD",
                                    },
                                },
                            },
                            "discount_config": {
                                "type": "object",
                                "required": ["percentage", "rule"],
                                "properties": {
                                    "percentage": {
                                        "type": "number",
                                        "default": 0,
                                        "description": "折扣率: 如 0.01 代表 1% 折扣",
                                    },
                                    "rule": {
                                        "type": "string",
                                        "enum": [
                                            "STANDARD",
                                            "AUTO_PAY",
                                            "HIGH_SA",
                                            "MEMBER",
                                        ],
                                        "description": "明確區分案件類型，如一般件或轉帳件",
                                        "default": "STANDARD",
                                    },
                                },
                            },
                            "sum_assured_tier": {
                                "type": "object",
                                "required": ["min", "max"],
                                "properties": {
                                    "min": {"type": "number", "default": 0},
                                    "max": {
                                        "type": "number",
                                        "default": 0,
                                        "description": "0 代表不限",
                                    },
                                },
                            },
                            "plans": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["plan_name"],
                                    "properties": {
                                        "plan_name": {"type": "string"},
                                        "plan_code": {"type": "string"},
                                        "premium": {
                                            "type": "number",
                                            "description": "單一保費數值",
                                        },
                                        "premium_structure": {
                                            "type": "object",
                                            "description": "複合型保費結構(起步價+增購價)",
                                            "properties": {
                                                "base_premium": {
                                                    "type": "number",
                                                    "description": "基礎保額對應保費",
                                                },
                                                "incr_premium": {
                                                    "type": "number",
                                                    "description": "增額基數對應保費",
                                                },
                                                "base_amt": {
                                                    "type": "number",
                                                    "description": "基礎保額額度",
                                                },
                                                "incr_unit_amt": {
                                                    "type": "number",
                                                    "description": "增額保額基數",
                                                },
                                                "description": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

    def extract_metadata(self, file) -> Dict[str, Any]:
        """提取僅 Metadata 部分，用於 Batch 模式"""
        prompt = """
        請僅從文件中提取 Metadata 信息（不要返回 rate_table）。
        請識別：product_name, product_family, product_code, pricing_basis, currency, unit_config, frequency_factors, benefit_period, max_insurance_age, occupation_limit, limit_logic, version 等欄位。
        """
        partial_schema = {
            "type": "object",
            "required": ["metadata"],
            "properties": {"metadata": self.get_schema()["properties"]["metadata"]},
        }
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=partial_schema,
                temperature=0.1,
            ),
        )
        return safe_json_loads(response.text, "Metadata 提取")

    def extract_rate_table_chunk(
        self, file, start_index=0, limit=20, is_page=False
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """提取費率表的指定範圍，用於 Batch 或單頁模式 (去重壓縮版)"""

        if is_page:
            prompt = f"請提取本頁文件中【所有】費率表的資料。請將本頁所有變動列資料完整萃取出來，但 variations 總數絕對不能超過 {limit} 筆！"
        else:
            prompt = f"請從文件中提取費率表的資料，目前只需要提取「第 {start_index + 1} 到 {start_index + limit} 筆變動列資料」。輸出的 variations 總列數絕對不能超過 {limit} 筆！"

        prompt += """
        Role: 資深保險精算與資料工程師
        
        Task: 
        精確提取 PDF 費率數據。你必須優先辨識「表格變動維度」並將其歸納至 `shared_attributes`，以極小化 `variations` 的冗餘。
        
        Extraction Logic (精算校準):
        1. **標籤優先與繼承策略**:
           - 掃描頁面最頂端括號內容。若出現「一般件」、「轉帳件」、「高保額調整」，必須反映在 `discount_config`。
           - 若表格出現「55歲還本/65歲還本」，必須填入 `variant_tags.return_age`。
           - 連續多列的「計畫別、繳費年期、折扣、性別」完全相同時，必須合併為同一個 `rate_block`。
        2. **折扣辨識**:
           - 若腳註提及「已含 1% 折扣」，請將 `discount_config` 的 percentage 設為 0.01 且 rule 為 HIGH_SA。
           - **禁止自行換算**: premium 填寫表內看到的數值。
        3. **區塊壓縮與拆分**:
           - 若表格為左右分欄（如左男右女），請拆分為兩個 `rate_block`，一個 gender 為 M，一個為 F。
        4. **極端值**:
           - 年齡「0歲」或「不滿1歲」統一設定為 age_start: 0, age_end: 0。
        """

        orig_rt_props = self.get_schema()["properties"]["rate_table"]["items"][
            "properties"
        ]
        orig_plan_props = orig_rt_props["plans"]["items"]["properties"]

        chunk_schema = {
            "type": "object",
            "properties": {
                "rate_blocks": {
                    "type": "array",
                    "description": "將分類屬性相同的列歸為同一個 block 以壓縮資料長度",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shared_attributes": {
                                "type": "object",
                                "description": "被這幾列資料所共用的維度，由該區塊內所有列共用的靜態維度",
                                "properties": {
                                    "gender": orig_rt_props["gender"],
                                    "occupation_level": orig_rt_props.get(
                                        "occupation_level", {"type": "integer"}
                                    ),
                                    "social_insurance": orig_rt_props[
                                        "social_insurance"
                                    ],
                                    "contract_year_type": orig_rt_props[
                                        "contract_year_type"
                                    ],
                                    "premium_period": orig_rt_props.get(
                                        "premium_period", {"type": "object"}
                                    ),
                                    "variant_tags": orig_rt_props.get(
                                        "variant_tags", {"type": "object"}
                                    ),
                                    "discount_config": orig_rt_props.get(
                                        "discount_config", {"type": "object"}
                                    ),
                                    "sum_assured_tier": orig_rt_props.get(
                                        "sum_assured_tier", {"type": "object"}
                                    ),
                                    "plan_name": orig_plan_props["plan_name"],
                                    "plan_code": orig_plan_props.get(
                                        "plan_code", {"type": "string"}
                                    ),
                                },
                            },
                            "variations": {
                                "type": "array",
                                "description": "逐列變動的細節數值",
                                "items": {
                                    "type": "object",
                                    "required": ["age_start"],
                                    "properties": {
                                        "age_start": orig_rt_props["age_start"],
                                        "age_end": orig_rt_props["age_end"],
                                        "premium": orig_plan_props.get(
                                            "premium", {"type": "number"}
                                        ),
                                        "premium_structure": orig_plan_props.get(
                                            "premium_structure", {"type": "object"}
                                        ),
                                    },
                                },
                            },
                        },
                    },
                }
            },
        }

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=chunk_schema,
                temperature=0.1,
            ),
        )
        parsed = safe_json_loads(
            response.text, f"Rate Table 區塊提取 (start={start_index}, limit={limit})"
        )

        # --- Python 端自動還原(Decompress)為一維正規矩陣 ---
        rate_table_flat = []
        for block in parsed.get("rate_blocks", []):
            shared = block.get("shared_attributes", {})
            variations = block.get("variations", [])
            for var in variations:
                record = {
                    "gender": shared.get("gender") or var.get("gender") or "BOTH",
                    "age_start": var.get("age_start", 0),
                    "age_end": var.get("age_end", var.get("age_start", 0)),
                    "occupation_level": shared.get("occupation_level", 0),
                    "social_insurance": shared.get("social_insurance", "BOTH"),
                    "contract_year_type": shared.get("contract_year_type", "BOTH"),
                    "plans": [],
                }

                # 合併 Shared 與 Variations 屬性 (優先採用 Variations)
                for field in [
                    "premium_period",
                    "variant_tags",
                    "discount_config",
                    "sum_assured_tier",
                ]:
                    if field in shared or field in var:
                        record[field] = var.get(field) or shared.get(field)

                plan = {
                    "plan_name": shared.get("plan_name", "未知計畫"),
                }
                if "plan_code" in shared:
                    plan["plan_code"] = shared["plan_code"]
                elif "plan_code" in var:
                    plan["plan_code"] = var["plan_code"]

                if "premium" in var:
                    plan["premium"] = var["premium"]
                if "premium_structure" in var:
                    plan["premium_structure"] = var["premium_structure"]

                record["plans"].append(plan)
                rate_table_flat.append(record)

        # 回傳雙版本：解壓後的一維陣列 與 原始壓縮的 rate_blocks
        return rate_table_flat, parsed.get("rate_blocks", [])

    def extract_page_by_page(
        self, local_pdf_path: str, batch_size: int = 10, full_file=None
    ) -> Dict[str, Any]:
        """按頁切分 PDF，逐頁向 API 請求提取以避免漏資料與 Token 限制"""
        from pypdf import PdfReader, PdfWriter
        import tempfile
        import os
        import time
        from google.genai import types

        # 1. 提取 Metadata (使用完整檔案，確保產品全面屬性正確)
        metadata_result = self.extract_metadata(full_file)
        metadata = metadata_result.get("metadata", {})

        rate_table = []
        rate_blocks = []

        reader = PdfReader(local_pdf_path)
        total_pages = len(reader.pages)

        print(f"    共偵測到 {total_pages} 頁 PDF，啟動分頁解析...")

        for i in range(total_pages):
            writer = PdfWriter()
            writer.add_page(reader.pages[i])

            page_fd, page_path = tempfile.mkstemp(suffix=".pdf")
            os.close(page_fd)
            # 寫入單頁 PDF
            with open(page_path, "wb") as f:
                writer.write(f)

            # 上傳單頁 PDF 至 Gemini
            page_file = self.client.files.upload(
                file=page_path,
                config=types.UploadFileConfig(display_name=f"page_{i + 1}.pdf"),
            )
            while page_file.state.name == "PROCESSING":
                time.sleep(2)
                page_file = self.client.files.get(name=page_file.name)

            # 單頁通常包含的資訊量較少，Limit 改大避免截斷合法資料
            page_limit = max(150, batch_size * 5)
            chunk_records, chunk_blocks = self.extract_rate_table_chunk(
                page_file, start_index=0, limit=page_limit, is_page=True
            )

            rate_table.extend(chunk_records)
            rate_blocks.extend(chunk_blocks)

            print(
                f"    [頁面 {i + 1}/{total_pages}] 解析完成，取得 {len(chunk_records)} 筆變動資料。"
            )

            # 清理暫存資源避免塞爆
            self.client.files.delete(name=page_file.name)
            os.remove(page_path)

        return {
            "metadata": metadata,
            "rate_table": rate_table,
            "rate_blocks": rate_blocks,
        }

    def extract(
        self, file, batch_size: int = 10, max_records: int = 2000
    ) -> Dict[str, Any]:
        if not batch_size or batch_size <= 0:
            # 完整解析（按需開啟 batch_size 才用分批模式）
            prompt = """
            Role:
            你是一位資深保險精算與資料工程師，專精於將各家保險公司複雜且非結構化的費率表（PDF、圖片、掃描檔）精確轉化為高精細度的費率矩陣 JSON。
            Task:
            請徹底分析附件文件（包含表格、腳註、條款說明），並根據下方嚴格的 [Extraction Rules] 產出符合 [JSON Schema] 的數據。
            
            [Extraction Rules]:
            1. 商品維度與計價判定 (Metadata)
            - currency: 幣別映射。根據標題或符號自動識別幣別，並轉換為 ISO 4217 標準代碼,若未提及，根據保險公司所在地推理（台灣公司預設為 TWD）
            - pricing_basis: 若有特定計畫名（如 HS-5, 方案A）設為 PLAN；若為每萬/每千保額計價，設為 UNIT。
            - calculation_logic: 觀察不同「單位」間的保費。若「單位 2」的保費等於「單位 1」的兩倍，設為 MULTIPLY；若不等於（非線性），即便名稱叫單位，也請設為 LOOKUP 並將其視為獨立 plan_name。
            - unit_config: 自動識別「單位」描述。若提及「萬元」則 unit_value: 10000,unit_text:"每萬元"；「千元」則 unit_value: 1000,unit_text:"每千元"；PLAN 模式，unit_value 預設為 1。
            - benefit_period (保障期間): 類型必須為 YEAR (年), AGE (至某歲), 或 LIFE (終身)。例如「至80歲滿期」對應 type: AGE, value: 80。
            - max_insurance_age: 最高承保年齡。從備註或規則中尋找最高投保年齡限制（如：最高可至 65 歲，填入 65）。
            - occupation_limit: 承保職業等級上限。若無標註，預設設為 6。
            - limit_logic: 請從腳註尋找投保限額（如：最低 10 萬，最高 100 萬，級距 1 萬）。

            2. 費率表核心處理 (Rate Table)
            - 年齡區間: 「30-34歲」拆解為 age_start: 30, age_end: 34。單一年齡則兩者相同。
            - 極端值: 「0歲」或「不滿1歲」統一設為 age_start: 0, age_end: 0。
            - 上限處理: 若為「65歲以上」，請查閱腳註中的最高續保年限（如 80 歲），設為 age_start: 65, age_end: 80。

            3. 性別與職業等級映射 (Gender & Occupation)
            - 性別: 男性映射為 M，女性映射為 F。若表格未區分性別（共用費率），務必設為 BOTH。
            - 職業等級: 識別費率適用的等級（1-6級）。若表格未標註職業差異（如一般住院醫療險），occupation_level 統一設為 0。

            4. 身分維度識別 (Social Insurance & Contract Year)
            - 職業等級: 若費率表依此區分，請正確填入。不分 -> 0。
            - 社會保險: 是否區分。有 -> Y, 無 -> N, 不分 -> BOTH。
            - 契約年度 (contract_year_type): FIRST_YEAR (首年), RENEWAL (續保), BOTH (不分)。
            - 繳費期間 (premium_period): 類型為 YEAR, AGE, 或 SINGLE。

            5. 保費結構與歸一化 (Premium Structure)
            - 單一保費: 若為固定數值，直接填入 premium。
            - 複合保費: 若包含「基礎保費 + 增額保費」，必須填寫 premium_structure。
            - base_amt: 起跳保額（如 10萬）。
            - base_premium: 起跳保額對應保費。
            - incr_unit_amt: 增額基數（如 每1萬）。
            - incr_premium: 增額對應保費。

            6. 投保限制邏輯提取 (Limit Logic)
            - 腳註識讀: 必須從備註中提取投保限額規則（如：最低 10 萬，最高 100 萬，每 1 萬為級距）。
            - 數值化: 將規則轉化為 min_unit, max_unit, step_unit。原始文字保留於 limit_text 備查。

            7. 繳費係數歸一化 (Frequency Factor Normalization)
            - 公式換算: 從備註提取繳費方式換算比例（半年/季/月繳）。
            - 基準統一: 無論原始公式是「對標年繳」或「階梯式遞迴（如月繳為季繳之0.34）」，必須自動執行數學換算，確保 frequency_factors 內的所有數值均為「相對於年繳保費」的最終乘數。
            - 預設值: 若未提及，使用業界慣例（年1.0 / 半0.52 / 季0.262 / 月0.088）。

            8. 數據清洗規範
            - 移除所有金額中的 ,、$、元、NT$或金額的逗號。
            - 所有數值欄位必須為 Number 或 Integer，不可包含單位字串。
            """
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=self.get_schema(),
                    temperature=0.1,
                ),
            )
            return safe_json_loads(response.text, "完整深度解析 (無 Batch)")

        # Batch 解析邏輯
        metadata_result = self.extract_metadata(file)
        metadata = metadata_result.get("metadata", {})

        rate_table = []
        rate_blocks = []
        current = 0
        last_chunk = None
        while current < max_records:
            chunk_records, chunk_blocks = self.extract_rate_table_chunk(
                file, start_index=current, limit=batch_size
            )
            if not chunk_records or not isinstance(chunk_records, list):
                break

            # 檢查模型是否因為幻覺失效而不斷回傳相同的重複資料
            if chunk_records == last_chunk:
                print(
                    f">>> 警告: 模型產生幻覺發生重複資料迴圈，已提早終止 (目前筆數 {current})。"
                )
                break
            last_chunk = chunk_records

            rate_table.extend(chunk_records)
            rate_blocks.extend(chunk_blocks)
            if len(chunk_records) < batch_size:
                break
            current += len(chunk_records)

        return {
            "metadata": metadata,
            "rate_table": rate_table,
            "rate_blocks": rate_blocks,
        }


class NegativeReasoningValidator:
    """Stage 2: 空值合理性自評模組"""

    def __init__(self, client, model_name="gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def validate_missing_fields(
        self, file, current_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        missing_fields = []
        metadata = current_data.get("metadata", {})
        for field, value in metadata.items():
            if value is None or value == "":
                missing_fields.append(field)

        if not missing_fields:
            return []

        prompt = f"文件回報缺失欄位：{missing_fields}。請再次掃描文件確認是否真不存在。輸出格式：{{'checks': [...]}}"

        # 加入嚴格 Schema 防止模型在找不到時產生幻覺（例如把整個表格重新印出）
        schema = {
            "type": "object",
            "properties": {"checks": {"type": "array", "items": {"type": "string"}}},
        }

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        return safe_json_loads(response.text, "Stage 2: 空值合理性自評").get(
            "checks", []
        )


class LogicCrossChecker:
    """Stage 3: 業務邏輯勾稽模組"""

    def check(self, data: Dict[str, Any]) -> List[str]:
        alerts = []
        metadata = data.get("metadata", {})
        rate_table = data.get("rate_table", [])
        max_age_meta = metadata.get("max_insurance_age")
        if max_age_meta:
            max_age_in_table = max(
                (item.get("age_end", 0) for item in rate_table), default=0
            )
            if max_age_in_table != max_age_meta:
                alerts.append(
                    f"CRITICAL: 邏輯勾稽失敗！Metadata 最大年齡 {max_age_meta} vs 費率表最大年齡 {max_age_in_table}。"
                )
        return alerts


class ConfidenceOrchestrator:
    """解耦調度中心"""

    def __init__(self, api_key: str = config.GEMINI_API_KEY):
        # 統一配置 API Key
        self.client = genai.Client(api_key=api_key)
        self.quality_eval = QualityEvaluator(client=self.client)
        self.extractor = RateExtractor(client=self.client)
        self.negative_check = NegativeReasoningValidator(client=self.client)
        self.logic_check = LogicCrossChecker()

    def _calculate_confidence_score(self, results: Dict[str, Any]) -> int:
        """根據各個階段的結果計算綜合信心分數 (0-100)"""
        # 1. 基礎分由文件品質決定 (Stage 0: 1-5 分)
        quality_eval = results.get("stages", {}).get("quality", {})
        quality_score = quality_eval.get("overall_score", 0)
        base_score = quality_score * 20  # 5星=100, 4星=80...

        # 2. 扣分項：邏輯衝突 (Stage 3)
        logic_alerts = results.get("stages", {}).get("logic_alerts", [])
        logic_penalty = len(logic_alerts) * 20

        # 3. 扣分項：疑似遺漏欄位 (Stage 2)
        missing_fields = results.get("stages", {}).get("negative_reasoning", [])
        # 如果是系統警告字串而非清單，也算作一個扣分點
        missing_penalty = (
            len(missing_fields) * 10 if isinstance(missing_fields, list) else 10
        )

        final_score = base_score - logic_penalty - missing_penalty
        return max(0, final_score)

    def process(self, file_path: str, batch_size: int = 10, eval_chunk_size: int = 20):
        # 解決 google-genai/httpx 處理非 ASCII 檔名的 multipart 編碼錯誤 (ascii codec error)
        ext = os.path.splitext(file_path)[1]
        temp_fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(temp_fd)
        shutil.copy2(file_path, temp_path)

        myfile = None
        results = {"file_name": os.path.basename(file_path), "stages": {}}
        try:
            # display_name 仍可正常指定原本的中文檔名
            myfile = self.client.files.upload(
                file=temp_path,
                config=types.UploadFileConfig(display_name=os.path.basename(file_path)),
            )
            while myfile.state.name == "PROCESSING":
                time.sleep(2)
                myfile = self.client.files.get(name=myfile.name)

            # Stage 0: Quality
            results["stages"]["quality"] = self.quality_eval.evaluate(myfile)

            # Stage 1: Extraction (Pure)
            if ext.lower() == ".pdf":
                print(f">>> 檔案格式為 PDF,進行分頁處理...")
                data = self.extractor.extract_page_by_page(
                    temp_path, batch_size=batch_size, full_file=myfile
                )
            else:
                print(f">>> 執行解析（Batch {batch_size} 筆）...")
                data = self.extractor.extract(myfile, batch_size=batch_size)
            results["extracted_data"] = data

            # Stage 2 & 3 (原 Stage 2 自評模組已依策略移除以提升效能)
            try:
                results["stages"]["negative_reasoning"] = (
                    self.negative_check.validate_missing_fields(myfile, data)
                )
            except Exception as e:
                print(f">>> [警告] 空值合理性自評發生例外錯誤，已跳過該環節: {e}")
                results["stages"]["negative_reasoning"] = [
                    f"系統警告: 空值自評模組執行失敗 ({e})，請務必手動核對所有欄位"
                ]

            try:
                results["stages"]["logic_alerts"] = self.logic_check.check(data)
            except Exception as e:
                print(f">>> [警告] 邏輯勾稽發生例外錯誤，已跳過該環節: {e}")
                results["stages"]["logic_alerts"] = [
                    f"系統警告: 業務邏輯勾稽模組執行失敗 ({e})，請手動確認費率與檔期合理性"
                ]

        finally:
            if myfile:
                self.client.files.delete(name=myfile.name)
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # 計算最終信心分數
        results["global_confidence_score"] = self._calculate_confidence_score(results)
        return results


def generate_audit_log(results: Dict[str, Any], **kwargs) -> str:
    """
    專為使用者與開發者設計的終端日誌產生器
    顯示文件維度、遺漏欄位以及業務邏輯異常
    """
    if not results:
        return "⚠️ 日誌生成失敗：無提取結果"

    log_lines = []

    # 1. 檔案與整體狀況
    filename = results.get("file_name", "Unknown File")
    quality_eval = results.get("stages", {}).get("quality", {})
    is_low_quality = quality_eval.get("is_low_quality", False)

    log_lines.append(f"====== 解析報告 ======")
    log_lines.append(f"📄 檔案名稱: {filename}")

    if is_low_quality:
        reason = quality_eval.get("reason", "未提供原因")
        log_lines.append(f"🔴 [警告] 文件基礎品質過低！原因: {reason}")
    else:
        log_lines.append(f"🟢 文件基礎品質檢測通過。")

    exceptions_found = 0

    # 2. 顯示系統級錯誤 (Stage 3 & 4)
    logic_alerts = results.get("stages", {}).get("logic_alerts", [])
    if logic_alerts:
        log_lines.append(f"\n====== 🚨 重大邏輯衝突 ======")
        for alert in logic_alerts:
            log_lines.append(f"- {alert}")
            exceptions_found += 1

    missing_fields = results.get("stages", {}).get("negative_reasoning", [])
    if missing_fields:
        log_lines.append(f"\n====== ❓ 疑似遺漏欄位 ======")
        log_lines.append(f"- AI 強烈懷疑文件中少了這些欄位: {missing_fields}")
        exceptions_found += 1

    if exceptions_found == 0 and not is_low_quality:
        log_lines.append("\n🎉 太棒了！未發現業務邏輯衝突與重要欄位遺漏。")
    else:
        total_issues = exceptions_found + (1 if is_low_quality else 0)
        log_lines.append(f"\n總結：共發現 {total_issues} 個需要人工覆核的關注點。")

    # 5. 信心評定 (New)
    confidence_score = results.get("global_confidence_score", "N/A")
    log_lines.append(f"🎯 最終信心評定: {confidence_score} 分")

    return "\n".join(log_lines)


if __name__ == "__main__":
    pass
