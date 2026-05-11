import json
from typing import List, Dict
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_3_SCHEMA


class LogicParserAgent(BaseAgent):
    """
    Agent 3: Logic Parser（核心）
    任務：只解析公式邏輯
    - trigger_condition, formula, recursive, payment_period, conditions
    - 不要碰 parameter metadata (交給 Agent 4)。
    - 不要碰 lookup dimensions (交給 Agent 5)。
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位資深精算工程師，負責將保單給付邏輯轉化為精確的條件與公式。
        ## 必須完整識別
        - 保單年度差異
        - 年齡區間差異
        - 事故發生階段差異
        - 給付期間差異
        - 保單狀態變化
        - 宣告利率變化
        - 已領取給付後的遞迴變化
        ## 嚴格任務限制
        1. 你只能解析核心的給付觸發條件 (trigger_condition)、給付公式 (formula)、遞迴性質 (recursive)、給付期間 (payment_period) 以及特殊限制 (conditions)。
        2. 絕對不要列出任何關於變數的 Metadata (那是下一個階段的任務)。
        3. 絕對不要列出查表維度 (Lookup dimensions)。
        4. 回傳的 JSON 必須嚴格遵照 Schema 要求。
        ## 複雜公式轉譯規則
        若給付邏輯隨條件變動，必須完整保留 IF-ELSE 分支，例如：
        ## 禁止
            - 合併不同年度邏輯
            - 簡化階段式公式
            - 遺漏條件限制

        ## 正確範例
        IF(policy_year <= 5)
        THEN insured_amount * 1.2
        ELSE insured_amount * 1.5
        ENDIF
        巢狀邏輯範例：
        IF(A)
        THEN
            IF(B) THEN X ELSE Y ENDIF
        ELSE Z
        ENDIF
        ## 遞迴給付判定規則
        若後一期給付依賴前一期、年金逐期增減、利率滾存、累積增值，則必須設定 `is_recursive: true`。
        遞迴範例: payment_n = payment_(n-1) * factor
        ## 注意
        保險條款的邏輯寫「一倍」代表的是「基準值本身」。例如條款寫「住院醫療日額的一倍乘以施作住院手術之該次實際住院日數」，就應該是 住院醫療日額 * 1 * 實際住院日數，而不是*2。
        """
        super().__init__(
            schema=AGENT_3_SCHEMA, system_instruction=instruction, **kwargs
        )

    def parse_logic(self, benefit_code: str, related_segments: List[dict]) -> dict:
        """
        為「單一」給付項目解析公式邏輯。
        :param benefit_code: 給付項目代碼
        :param related_segments: 該給付項目所關聯的條文片段
        :return: 依照 AGENT_3_SCHEMA 輸出的結果
        """
        content = (
            f"目前正在解析的給付項目代碼: {benefit_code}\n\n"
            f"相關條文內容:\n{json.dumps(related_segments, ensure_ascii=False, indent=2)}"
        )

        result = self.execute([content])
        if result:
            result["benefit_code"] = benefit_code
        return result or {}
