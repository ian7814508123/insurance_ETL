import json
from typing import List
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_4_SCHEMA


class ParameterGraphBuilderAgent(BaseAgent):
    """
    Agent 4: Parameter Graph Builder
    任務: 只做 "變數系統" (定義參數名稱、來源類型、資料型態及相依性)
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位系統架構師，專門負責從保單邏輯中抽取所有的變數與參數（Parameter Graph）。
        ## 嚴格任務限制
        1. 你的唯一任務是建立「變數系統」，列出公式與條件中提到的所有變數。
        2. 你需要明確指出每個變數的來源類型 (source_type)，如 INPUT, CONSTANT, FORMULA, TABLE_LOOKUP, SYSTEM_DERIVED。
        3. 你需要明確指出變數之間的相依性 (depends_on)。
        4. 絕對不要重新撰寫給付公式或條件，只需要列出變數。
        ## 深度參數追溯規則
        必須識別兩類變數：
        - 外生變數 (External Variables): 性別、投保年齡、職業等級、保額、宣告利率、手術等級、保單年度、經過年度等。
        - 內生參數 (Internal Parameters): 保單價值準備金、累積紅利、已領年金、保費總和、解約金比例等。
        若某變數來源為 FORMULA，請附上推導公式 (formula_definition)。
        """
        super().__init__(
            schema=AGENT_4_SCHEMA, system_instruction=instruction, **kwargs
        )

    def build_parameters(
        self, benefit_code: str, logic_structure: dict, related_segments: List[dict]
    ) -> dict:
        """
        為「單一」給付項目解析變數系統。
        :param benefit_code: 給付項目代碼
        :param logic_structure: Agent 3 產生的邏輯結構 (供 LLM 參考)
        :param related_segments: 相關條文 (供 LLM 參考原文)
        :return: 依照 AGENT_4_SCHEMA 輸出的變數清單
        """
        content = (
            f"給付項目: {benefit_code}\n\n"
            f"已解析出的邏輯公式結構:\n{json.dumps(logic_structure, ensure_ascii=False, indent=2)}\n\n"
            f"原始關聯條文:\n{json.dumps(related_segments, ensure_ascii=False, indent=2)}\n\n"
            "請根據上述資訊，列出所有參與運算的參數與變數。"
        )

        result = self.execute([content])
        if result:
            result["benefit_code"] = benefit_code
        return result or {}
