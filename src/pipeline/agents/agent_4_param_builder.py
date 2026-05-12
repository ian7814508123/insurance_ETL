import json
from typing import List
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_4_SCHEMA
from pipeline.context import PipelineContext


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
        5. **強制命名規範**：如果你要建立的變數與「既有標準參數表」中的概念相符，你**必須**直接使用該表中定義好的英文字段名稱 (parameter_name)。絕對不可以創造新的或相似的名稱 (例如已有 HOSPITAL_DAILY_AMOUNT 就絕對不能用 HOSPITALIZATION_BENEFIT)。
        ## 深度參數追溯規則
        必須識別兩類變數：
        - 外生變數 (External Variables): 性別、投保年齡、職業等級、保額、宣告利率、手術等級、保單年度、經過年度等。
        - 內生參數 (Internal Parameters): 保單價值準備金、累積紅利、已領年金、保費總和、解約金比例等。
        若某變數來源為 FORMULA，請附上推導公式 (formula_definition)。
        ## value_binding 判定規則（核心指令）
        每個參數**必須**填寫 `value_binding`，用來說明這個值從哪裡取得。
        請依照以下規則嚴格判斷：

        ### USER_INPUT（使用者需要填入）
        **條件**：條款中並未給出固定數值，這個值因人而異，須在報價或試算時向使用者詢問。
        **典型例子**：
        - 年齡（insured_age）：每個人不同
        - 性別（gender）：每個人不同
        - 投保保額（sum_insured）：由要保人自訂
        - 職業等級（occupation_class）：因人而異
        - 保單年度（policy_year）：系統運算時隨時間變動，須由使用者在試算當下指定

        ### POLICY_FIXED（條款已明確記載定值）
        **條件**：條款原文中有明確的固定數字，全保單適用，不因被保人不同而變動。
        此時必須將已知數值填入 `fixed_value` 欄位。
        **典型例子**：
        - 年貼現率 2.25% → binding_type: "POLICY_FIXED", fixed_value: "0.0225"
        - 等待期 90 天 → binding_type: "POLICY_FIXED", fixed_value: "90"
        - 最高給付 60 期 → binding_type: "POLICY_FIXED", fixed_value: "60"
        - 住院日額的「2 倍」中的倍數係數 → binding_type: "POLICY_FIXED", fixed_value: "2"

        ### SYSTEM_CALCULATED（由其他變數公式推導）
        **條件**：這個值不是由使用者輸入，也不是條款固定數值，而是由其他已知變數經公式計算得出。
        **典型例子**：
        - 解約金 = 保單價值準備金 × 解約費用率（由其他參數推導）
        - 分期保險金餘額 = 前期餘額 + 利息 − 已領金額

        ### 判定優先順序
        1. 先看條款文字：數字是否明確寫死？→ POLICY_FIXED，並記錄 fixed_value
        2. 這個值是否每位被保人/投保人可能不同？→ USER_INPUT
        3. 其他（可從已知參數推導）→ SYSTEM_CALCULATED
        """
        super().__init__(
            schema=AGENT_4_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    def build_parameters(
        self,
        benefit_code: str,
        logic_structure: dict,
        related_segments: List[dict],
        context: PipelineContext,
    ) -> dict:
        """
        為「單一」給付項目解析變數系統。
        :param benefit_code: 給付項目代碼
        :param logic_structure: Agent 3 產生的邏輯結構 (供 LLM 參考)
        :param related_segments: 相關條文 (供 LLM 參考原文)
        :param context: 包含全域定義與標準參數名的上下文
        :return: 依照 AGENT_4_SCHEMA 輸出的變數清單
        """
        global_defs_str = json.dumps(
            context.global_definitions, ensure_ascii=False, indent=2
        )
        std_params_str = json.dumps(
            context.standard_parameters, ensure_ascii=False, indent=2
        )

        content = (
            f"給付項目: {benefit_code}\n\n"
            f"--- 既有標準參數表 (強制參考) ---\n"
            f"以下是已經決定好的標準參數名 (格式: 中文 -> 英文)。\n"
            f"若你要建立的變數概念與下表中的中文相符，請**務必**使用對應的英文名！\n"
            f"{std_params_str}\n\n"
            f"--- 全域商品名詞定義參考 ---\n"
            f"{global_defs_str}\n\n"
            f"--- 已解析出的邏輯公式結構 ---\n"
            f"{json.dumps(logic_structure, ensure_ascii=False, indent=2)}\n\n"
            f"--- 原始關聯條文 ---\n"
            f"{json.dumps(related_segments, ensure_ascii=False, indent=2)}\n\n"
            "請根據上述資訊，列出所有參與運算的參數與變數。"
        )

        result = self.execute([content])
        if result:
            result["benefit_code"] = benefit_code
        return result or {}
