import json
from typing import List, Dict
from pipeline.base_agent import BaseAgent
from pipeline.context import PipelineContext


class ParameterHarvesterAgent(BaseAgent):
    """
    Agent 2.5: Global Parameter Harvester
    任務: 在並行處理前，批次掃描所有條文，建立全域統一的變數對照表。
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位保險系統分析師，負責建立全域變數對照表。
        你的任務是從提供的多段保單條文中，找出所有涉及計算、條件判斷的「技術名詞」或「參數」，並為其建立統一的英文代碼 (Code)。

        ## 任務規則
        1. 掃描所有條文，識別出如「住院日額」、「保單年度」、「投保年齡」、「手術倍數」等名詞。
        2. 為每個名詞分配一個唯一、直觀的大寫英文底線代碼 (例如: HOSPITAL_DAILY_AMOUNT)。
        3. 如果不同條文使用了相同意思但字面上略有不同的名詞，請將其歸併為同一個 Code。
        4. **輸出格式**：回傳一個對照表列表，每個項目包含 `zh_name` 與 `en_code`。

        ## 命名範例
        - 住院日額 -> HOSPITAL_DAILY_AMOUNT
        - 投保金額 -> SUM_INSURED
        - 住院日數 -> HOSPITALIZATION_DAYS
        - 意外傷害事故 -> ACCIDENTAL_INJURY_EVENT
        """
        super().__init__(
            schema={
                "type": "array",
                "description": "參數對照表列表",
                "items": {
                    "type": "object",
                    "required": ["zh_name", "en_code"],
                    "properties": {
                        "zh_name": {"type": "string", "description": "中文名詞"},
                        "en_code": {"type": "string", "description": "英文代碼"}
                    }
                }
            },
            system_instruction=instruction,
            temperature=0.0,
            **kwargs,
        )

    async def harvest(self, segments: List[dict], context: PipelineContext) -> Dict[str, str]:
        """
        批次掃描段落，回傳統一對照表。
        """
        # 1. 提取名詞定義參考
        base_defs_summary = [
            {"code": d.get("code"), "display_name": d.get("display_name")}
            for d in context.global_definitions
        ]
        
        # 2. 提取基礎理賠項目參考 (包含可能的公式變數提示)
        base_items_summary = [
            {"code": i.get("code"), "display_name": i.get("display_name")}
            for i in context.base_claim_items
        ]

        # 3. 組合參考內容
        references = {
            "global_definitions": base_defs_summary,
            "base_claim_items": base_items_summary
        }

        # 為了節省 Token，我們只提取文本部分
        combined_text = "\n---\n".join([s.get("text", "") for s in segments])

        content = (
            f"--- 系統標準庫參考 (名詞與項目) ---\n"
            f"請參考以下標準庫，若條文中的變數或理賠概念與其相符，請優先使用對應的 Code：\n"
            f"{json.dumps(references, ensure_ascii=False, indent=2)}\n\n"
            f"--- 待掃描條文 ---\n{combined_text}"
        )

        # 執行 Agent
        result_list = await self.async_execute([content])
        
        # 將 [{zh_name: "xxx", en_code: "yyy"}, ...] 轉換為 {"xxx": "yyy"}
        mapping = {}
        if isinstance(result_list, list):
            for item in result_list:
                zh = item.get("zh_name")
                en = item.get("en_code")
                if zh and en:
                    mapping[zh] = en
                    
        return mapping
