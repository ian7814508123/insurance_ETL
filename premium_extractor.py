from google import genai
from google.genai import types
import json
import time
import os
import tempfile
import shutil
from typing import Dict, Any, List
import concurrent.futures
import config
from pypdf import PdfReader, PdfWriter


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
            "reason": "說明原因 (中文)"
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


class AgentA:
    """第一階段：Agent A (Metadata & Strategy Agent)
    任務：分析 PDF 前幾頁，確認產品屬性，並為 Agent B 制定「提取策略」。
    """

    def __init__(self, client, model_name="gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def get_metadata_schema(self) -> Dict[str, Any]:
        """定義 Metadata Schema"""
        return {
            "type": "object",
            "required": [
                "product_name",
                "product_family",
                "currency",
                "pricing_basis",
                "unit_config",
            ],
            "properties": {
                "product_name": {
                    "type": "string",
                    "description": "純淨的產品全名，不應包含「一般件」或「第X頁」等字眼",
                },
                "product_code": {
                    "type": "string",
                    "description": "商品代碼/報備編號",
                },
                "product_family": {
                    "type": "string",
                    "enum": [
                        "LIFE",
                        "MEDICAL",
                        "ACCIDENT",
                        "CANCER",
                        "CRITICAL_ILLNESS",
                        "OTHER",
                    ],
                    "description": "產品主類別",
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
        }

    def get_strategy_schema(self) -> Dict[str, Any]:
        """定義 Extraction Strategy Schema"""
        return {
            "type": "object",
            "properties": {
                "layout_type": {
                    "type": "string",
                    "enum": ["SINGLE_COLUMN", "MULTI_COLUMN", "BI_DIRECTIONAL"],
                },
                "key_column_mapping": {
                    "type": "object",
                    "description": "標題列與 Schema 欄位的映射關係",
                },
                "parsing_hints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "給 Agent B 的具體提取指令",
                },
            },
        }

    def analyze(self, contents: List[Any]) -> Dict[str, Any]:
        """執行分析，取得 Metadata 與 Strategy"""
        prompt = """
        作為一個保險精算專家 (Metadata 專家)，請分析此保險文件的「前幾頁」。
        你的任務是：
        1. 提取產品 Metadata。
        2. 為後續的「費率大規模提取員 (Agent B)」制定精確的「提取策略 (extraction_strategy)」。

        策略應包含：
        - 表格如何排列（例如：左側為男性費率，右側為女性費率）。
        - 哪些欄位需要特別注意（例如：有社保與無社保的分別）。
        - 腳註中是否有隱藏的計算係數或規則。
        """
        combined_schema = {
            "type": "object",
            "required": ["metadata", "extraction_strategy"],
            "properties": {
                "metadata": self.get_metadata_schema(),
                "extraction_strategy": self.get_strategy_schema(),
            },
        }

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents + [prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=combined_schema,
                temperature=0.1,
            ),
        )
        return safe_json_loads(response.text, "Agent A: Metadata & Strategy")


class AgentB:
    """第二階段：Data Extraction Agent
    任務：接收 Agent A 的 extraction_strategy，對費率頁面進行提取。
    """

    def __init__(self, client, model_name="gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def extract_page(
        self, contents: List[Any], strategy: Dict[str, Any], page_range: str = None
    ) -> List[Dict[str, Any]]:
        """根據策略提取單頁/多頁費率"""
        strategy_str = json.dumps(strategy, ensure_ascii=False, indent=2)

        page_instruction = ""
        if page_range:
            page_instruction = f"\n\n [專注頁面提醒] :\n請**特別針對第 {page_range} 頁**的內容進行分析與提取，忽略其他頁面的數據！\n\n"

        prompt = f"""
        作為費率提取員，請根據以下「提取策略」提取費率資料：
        {page_instruction}
        [提取策略 (Extraction Strategy)]:
        {strategy_str}
        
        任務要求：
        1. 優先辨識「表格共同維度」並歸納至 `shared_attributes`。
        2. 嚴格遵守策略中的 `parsing_hints`。
        3. 輸出 `rate_blocks` 陣列，每個 block 代表一組具有相同屬性的費率。
        4. 歲數處理：
           - 單一歲數：`age_start`: 20, `age_end`: 20
           - 歲數區間：`age_start`: 0, `age_end`: 4
           - 極端值:年齡「0歲」或「不滿1歲」統一設定為 age_start: 0, age_end: 0。
           - 例外處理:續保件通常沒有0歲 (常表示 0:"-" 或從1歲開始),若遇到時跳過該列。
        5. `scenario_name`: 描述屬性的組合（如：集體彙繳+自動轉帳件）。
        6. `scenario_description`: 詳細描述屬性的判斷準則。
        """

        schema = {
            "type": "object",
            "properties": {
                "rate_blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["shared_attributes", "variations"],
                        "properties": {
                            "shared_attributes": {
                                "type": "object",
                                "required": [
                                    "is_member",
                                    "is_auto_pay",
                                    "is_high_sa",
                                    "scenario_name",
                                    "premium_period",
                                    "occupation_level",
                                    "social_insurance",
                                ],
                                "description": "本頁/本區塊共同的屬性標籤",
                                "properties": {
                                    "is_member": {
                                        "type": "boolean",
                                        "description": "是否為集體彙繳",
                                    },
                                    "is_auto_pay": {
                                        "type": "boolean",
                                        "description": "是否為自動轉帳",
                                    },
                                    "is_high_sa": {
                                        "type": "boolean",
                                        "description": "是否為高保額折扣件",
                                    },
                                    "scenario_name": {
                                        "type": "string",
                                        "description": "方案組合名稱",
                                    },
                                    "scenario_description": {
                                        "type": "string",
                                        "description": "方案組合詳細描述",
                                    },
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
                                        "required": ["type", "value"],
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
                                        "description": "其他變動屬性標籤",
                                    },
                                    "other_tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "其他變動屬性字串列表",
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
                                },
                            },
                            "variations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["gender", "age_start", "premium"],
                                    "properties": {
                                        "gender": {
                                            "type": "string",
                                            "enum": ["M", "F", "BOTH"],
                                        },
                                        "age_start": {
                                            "type": "integer",
                                            "description": "起始歲數",
                                        },
                                        "age_end": {
                                            "type": "integer",
                                            "description": "結束歲數 (與 age_start 相同代表單一歲數)",
                                        },
                                        "age_display": {
                                            "type": "string",
                                            "description": "原始文本顯示的歲數 (如 0-4歲)",
                                        },
                                        "premium": {"type": "number"},
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
                }
            },
        }

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents + [prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        parsed = safe_json_loads(response.text, "Agent B: Rate Block Extraction")
        return parsed.get("rate_blocks", [])

    @staticmethod
    def decompress_blocks(rate_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """將壓縮的 rate_blocks 還原為一維矩陣 (內部勾稽用)"""
        flat_table = []
        for block in rate_blocks:
            shared = block.get("shared_attributes", {})
            variations = block.get("variations", [])
            for var in variations:
                record = {**shared, **var}
                flat_table.append(record)
        return flat_table


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
        rate_blocks = data.get("rate_blocks", [])

        # 內部還原費率表以便勾稽
        rate_table = AgentB.decompress_blocks(rate_blocks)

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
    """二階段 Agent 協作調度中心"""

    def __init__(self, api_key: str = config.GEMINI_API_KEY):
        # 統一配置 API Key
        self.client = genai.Client(api_key=api_key)
        self.quality_eval = QualityEvaluator(client=self.client)
        self.agent_a = AgentA(client=self.client)
        self.agent_b = AgentB(client=self.client)
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

            # Stage 1: Extraction (二階段 Agent A & B)
            # Step 1: 呼叫 Agent A (Metadata & Strategy)
            print(">>> 準備 Agent A 分析環境...")
            is_pdf = ext.lower() == ".pdf"

            if is_pdf:
                reader = PdfReader(temp_path)
                total_pages = len(reader.pages)
                preview_count = min(3, total_pages)
                print(
                    f"    [PDF 模式] 總頁數 {total_pages}, 選取前 {preview_count} 頁 (Inline) 作為 Agent A 預覽..."
                )

                writer = PdfWriter()
                for i in range(preview_count):
                    writer.add_page(reader.pages[i])

                with tempfile.TemporaryFile(suffix=".pdf") as tf:
                    writer.write(tf)
                    tf.seek(0)
                    preview_bytes = tf.read()

                agent_a_contents = [
                    types.Part.from_bytes(
                        data=preview_bytes, mime_type="application/pdf"
                    )
                ]
            else:
                agent_a_contents = [myfile]

            print(">>> 執行 Agent A 分析 (Metadata & Strategy)...")
            a_output = self.agent_a.analyze(agent_a_contents)
            metadata = a_output.get("metadata", {})
            strategy = a_output.get("extraction_strategy", {})

            # Step 2 & 3: 併發呼叫 Agent B (Batch Extraction)
            all_rate_blocks = []
            if is_pdf:
                print(
                    f">>> 檔案格式為 PDF, 共 {total_pages} 頁, 啟動 Agent B 併發分批解析 (API File 模式)..."
                )
                page_chunks = []
                # 為防止單次請求 Output Token 超過上限 (8192) 導致 JSON 截斷，直接改回單頁逐頁提取機制
                # 同時搭配下方的 concurrent.futures 繼續保有極速的併發效率
                for page_num in range(1, total_pages + 1):
                    page_chunks.append((page_num, page_num, str(page_num)))

                def extract_chunk(chunk_info):
                    _start, _end, r_str = chunk_info
                    print(
                        f"    [頁面 {r_str}/{total_pages}] (API File) Agent B 提取中..."
                    )
                    # 直接傳入 myfile (File object) 並透過 prompt 鎖定處理範圍
                    return self.agent_b.extract_page(
                        [myfile], strategy, page_range=r_str
                    )

                # 採用 concurrent.futures 併發處理以避免循序導致速度過慢
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    for blocks in executor.map(extract_chunk, page_chunks):
                        if blocks:
                            all_rate_blocks.extend(blocks)
            else:
                # 圖片或單頁
                print(">>> 執行 Agent B 提取 (單文件模式)...")
                blocks = self.agent_b.extract_page([myfile], strategy)
                all_rate_blocks.extend(blocks)

            # Step 4: 合併結果
            data = {
                "metadata": metadata,
                "extraction_strategy": strategy,
                "rate_blocks": all_rate_blocks,
                "rate_table": AgentB.decompress_blocks(all_rate_blocks),
            }
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
