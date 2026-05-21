import sys
from pathlib import Path
from typing import List, Any
import asyncio
import datetime
import json
from google import genai
from google.genai import types

# Ensure pipeline modules can be imported
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.append(str(src_path))

import config
from pipeline.agents.agent_1_segmenter import ClaimLocatorAgent
from pipeline.agents.agent_2_registry import BenefitRegistryAgent
from pipeline.agents.agent_2_5_harvester import ParameterHarvesterAgent
from pipeline.agents.agent_3_logic_parser import LogicParserAgent
from pipeline.agents.agent_4_param_builder import ParameterGraphBuilderAgent
from pipeline.agents.agent_5_lookup_modeler import LookupModelerAgent
from pipeline.context import PipelineContext
from definition_extractor import DefinitionExtractor


class PipelineOrchestrator:
    def __init__(self):
        self.agent_1 = ClaimLocatorAgent()
        self.agent_2 = BenefitRegistryAgent()
        self.agent_2_5 = ParameterHarvesterAgent()
        self.agent_3 = LogicParserAgent()
        self.agent_4 = ParameterGraphBuilderAgent()
        self.agent_5 = LookupModelerAgent()

    def process(self, document_contents: List[Any], base_info: dict) -> dict:
        """
        同步入口點，封裝了非同步執行邏輯。
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # 如果已經在運行中的事件迴圈（例如 Jupyter 或某些框架），使用不同的方式
            import nest_asyncio

            nest_asyncio.apply()

        return loop.run_until_complete(self.async_process(document_contents, base_info))

    def detect_product_type(self, client: genai.Client, document_contents: List[Any]) -> str:
        """前處理：讀取 PDF/條款前置內容（1-2頁），利用 LLM 自動判定商品險種"""
        sample_content = []
        if document_contents:
            # 取得前兩頁作為判定依據
            sample_content = document_contents[:2]

        schema = {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "enum": ["health", "injury", "investment", "life_annuity"],
                    "description": "HEALTH (健康醫療險), INJURY (傷害意外險), INVESTMENT (投資型保險), LIFE_ANNUITY (壽險與年金險)"
                },
                "reason": {
                    "type": "STRING",
                    "description": "判斷的簡短依據"
                }
            },
            "required": ["category", "reason"]
        }

        prompt = (
            "你是一個專業的保險條款險種分類專家。請閱讀以下保險商品前兩頁的條文，"
            "精確判定此商品最符合以下哪一種險種分類：\n"
            "- HEALTH: 健康醫療險（包含住院、手術、醫療日額、癌症、特定傷病、長期照顧等）。\n"
            "- INJURY: 傷害意外險（遭受外來、突發、非疾病意外事故致身體蒙受傷害、骨折、失能或身故）。\n"
            "- INVESTMENT: 投資型保險（包含專設帳戶、配置、單位淨值等自主投資性質商品）。\n"
            "- LIFE_ANNUITY: 壽險與年金險（定期/終身壽險、還本生存金、遞延/即期年金等商品）。"
        )

        try:
            response = client.models.generate_content(
                model=config.DEFAULT_MODEL,
                contents=[prompt] + sample_content,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                )
            )
            res = json.loads(response.text)
            category = res.get("category", "health").lower()
            print(f"  -> 險種判定理由: {res.get('reason')}")
            return category
        except Exception as e:
            print(f"  -> 險種自動判定失敗: {e}，預設歸類為 health。")
            return "health"

    async def async_process(
        self, document_contents: List[Any], base_info: dict
    ) -> dict:
        print("[0/6] Running Definition Extractor: 萃取名詞定義...")
        context = PipelineContext()
        def_extractor = DefinitionExtractor()

        # 1. 判定商品險種
        print("  -> 開始進行商品險種自動判定 (讀取前兩頁內容)...")
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        detected_category = self.detect_product_type(client, document_contents)
        print(f"  -> 險種自動判定結果：【{detected_category.upper()}】")

        # 2. 依據 Method 1 載入對應子詞庫
        definitions_dir = Path(__file__).parent.parent.parent / "data" / "definitions"
        
        # 主要載入：通用型 + 該商品險種類型
        primary_categories = ["general", detected_category]
        primary_defs = []
        for cat in primary_categories:
            file_name = "base_definition_investment.json" if cat == "investment" else f"base_definitions_{cat}.json"
            path = definitions_dir / file_name
            primary_defs.extend(def_extractor.load_definitions(str(path)))
        print(f"  -> 載入主要子詞庫 ({'+'.join(primary_categories)})：共 {len(primary_defs)} 筆名詞定義做為比對基準")

        # 備用載入：其餘三種險種類型
        fallback_categories = [c for c in ["health", "injury", "investment", "life_annuity"] if c != detected_category]
        fallback_defs = []
        for cat in fallback_categories:
            file_name = "base_definition_investment.json" if cat == "investment" else f"base_definitions_{cat}.json"
            path = definitions_dir / file_name
            fallback_defs.extend(def_extractor.load_definitions(str(path)))
        print(f"  -> 載入備用子詞庫 ({'+'.join(fallback_categories)})：共 {len(fallback_defs)} 筆名詞定義以備後續比對")

        # 讀取理賠項目的基礎定義檔
        base_claim_items_path = (
            Path(__file__).parent.parent.parent
            / "data"
            / "definitions"
            / "base_claim_items.json"
        )
        try:
            with open(base_claim_items_path, "r", encoding="utf-8") as f:
                context.base_claim_items = json.load(f)
            print(
                f"  -> 載入 {len(context.base_claim_items)} 筆基礎理賠項目做為對齊基準"
            )
        except Exception as e:
            print(f"Warning: Failed to load base_claim_items.json: {e}")

        # 呼叫 LLM 進行提取（傳入主要詞庫進行優先對齊）
        extracted_defs = def_extractor.extract_definitions(
            content=document_contents,
            context_definitions=primary_defs,
            level=base_info.get("level", "PRODUCT"),
            product_code=base_info.get("product_code"),
        )
        extracted_defs = extracted_defs or []

        # 3. 備用詞庫 fallback 比對：如果在主要詞庫沒中，則循序比對其餘三種備用詞庫
        fallback_hit_count = 0
        for item in extracted_defs:
            if item.get("classification") == "NEW_GENERAL":
                display_name = item.get("display_name", "")
                matched_fallback = None
                for fb_def in fallback_defs:
                    fb_display = fb_def.get("display_name", "")
                    fb_synonyms = fb_def.get("synonym_map", [])
                    if display_name == fb_display or display_name in fb_synonyms:
                        matched_fallback = fb_def
                        break
                
                if matched_fallback:
                    fallback_hit_count += 1
                    item["classification"] = "EXISTING_MATCH"
                    item["code"] = matched_fallback.get("code")
                    item["description"] = matched_fallback.get("description", item.get("description"))
                    item["base_definition"] = matched_fallback.get("base_definition", item.get("base_definition"))
                    item["parameter"] = matched_fallback.get("parameter", item.get("parameter", {}))
                    item["synonym_map"] = list(set(item.get("synonym_map", [])).union(set(matched_fallback.get("synonym_map", []))))
                    print(f"  -> 【備用詞庫命中】新名詞「{display_name}」成功對齊至備用詞庫中的既存名詞 {item['code']}")

        if fallback_hit_count > 0:
            print(f"  -> 透過備用詞庫成功對齊與修正了 {fallback_hit_count} 筆名詞定義分類")

        context.global_definitions = extracted_defs
        print(f"  -> 成功萃取 {len(context.global_definitions)} 筆名詞定義")

        print("[1/6] Running Agent 1: 條文切片...")
        segments = await self.agent_1.extract_segments(document_contents)
        if not segments:
            print("Warning: Agent 1 未回傳任何段落。")

        print(
            f"[2/6] Running Agent 2: 建立 Benefit Registry... (共 {len(segments)} 段條文)"
        )
        registry = await self.agent_2.build_registry(segments, context)
        if not registry:
            print("Warning: Agent 2 未找到任何給付項目。")
            return self._compose_final_schema(base_info, [], context)

        print("[2.5/6] Running Agent 2.5: 全域變數收割 ...")
        global_params = await self.agent_2_5.harvest(segments, context)
        if global_params:
            for zh, en in global_params.items():
                context.add_standard_parameter(zh, en)
            print(f"  -> 成功預收割 {len(global_params)} 個標準變數")

        print(f"\n>>> 開始並行處理 {len(registry)} 個給付項目 <<<\n")

        # 建立任務列表
        tasks = []
        for item in registry:
            tasks.append(
                self._process_single_benefit(
                    item, segments, context, document_contents, base_info
                )
            )

        # 並行執行所有給付項目的解析
        benefits_data = await asyncio.gather(*tasks)

        print("\n[6/6] 腳本執行: 組裝最終 JSON Schema ...")
        final_output = self._compose_final_schema(base_info, benefits_data, context)
        return final_output

    async def _process_single_benefit(
        self,
        item: dict,
        segments: list,
        context: PipelineContext,
        document_contents: list,
        base_info: dict,
    ) -> dict:
        benefit_code = item.get("benefit_code", "UNKNOWN")

        # 關聯段落
        seg_refs = item.get("segment_refs", [])
        related_segments = [s for s in segments if s.get("segment_id") in seg_refs]

        # Step 3: 邏輯解析
        print(f"  [Task Start] {benefit_code}: Agent 3 邏輯解析")
        logic_struct = await self.agent_3.parse_logic(
            benefit_code, related_segments, context
        )

        # Step 4: 參數變數
        print(f"  [Task Mid]   {benefit_code}: Agent 4 變數與相依性")
        params = await self.agent_4.build_parameters(
            benefit_code, logic_struct, related_segments, context
        )

        # 註冊變數到 Context (注意: asyncio 雖然是單執行緒，但在更新共用 context 時仍需留意)
        if params and "parameters" in params:
            context.register_parameters_from_agent4(params["parameters"])

        # Step 5: 附表查表
        print(f"  [Task End]   {benefit_code}: Agent 5 附表查表建模")
        lookups = await self.agent_5.model_lookup(
            benefit_code, params, document_contents
        )

        return {
            "registry_info": item,
            "logic": logic_struct,
            "parameters": params,
            "lookups": lookups,
            "related_segments": related_segments,
        }

    def _compose_final_schema(
        self, base_info: dict, benefits_data: list, context: PipelineContext
    ) -> dict:
        """
        組裝最終輸出格式
        """
        final_items = []
        for b_data in benefits_data:
            reg = b_data.get("registry_info", {})
            log = b_data.get("logic", {})
            param = b_data.get("parameters", {})

            # 如果 Agent 4 有提供校正後的邏輯，優先使用它，防止「斷鍵」
            corrected_logic = param.get("logic_structure")
            final_logic = (
                corrected_logic if corrected_logic else log.get("logic_structure", {})
            )

            lookup = b_data.get("lookups", {})
            segs = b_data.get("related_segments", [])

            base_def = reg.get("base_definition")
            if not base_def:
                base_def = "\n".join([s.get("text", "") for s in segs])

            source_ref = {}
            if segs:
                first_seg = segs[0]
                source_ref = {
                    "document_name": base_info.get("document_name"),
                    "page_start": first_seg.get("page_start"),
                    "page_end": first_seg.get("page_end"),
                    "section_title": first_seg.get("section"),
                }

            benefit_code = reg.get("benefit_code", "")
            base_codes = {b.get("code") for b in context.base_claim_items}
            classification = (
                "EXISTING_MATCH" if benefit_code in base_codes else "NEW_GENERAL"
            )

            item = {
                "type": "理賠項目定義",
                "code": benefit_code,
                "display_name": reg.get("display_name", ""),
                "level": base_info.get("level", "PRODUCT"),
                "classification": classification,
                "payment_type": reg.get("payment_type"),
                "base_definition": base_def,
                "source_reference": source_ref,
                "logic_structure": final_logic,
                "parameters": param.get("parameters", []),
                "lookup_tables": lookup.get("lookup_tables", []),
                "metadata": {
                    "parser_version": "2.0.0-agent-pipeline",
                    "schema_version": "1.0.0",
                    "created_at": datetime.datetime.now().isoformat(),
                    "review_status": "AUTO_EXTRACTED",
                },
            }
            final_items.append(item)

        return {
            "global_definitions": context.global_definitions,
            "claim_items": final_items,
        }
