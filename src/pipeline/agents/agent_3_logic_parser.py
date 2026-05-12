import json
from typing import List, Dict
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_3_SCHEMA
from pipeline.context import PipelineContext


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
        ## 禁止改寫 base_definition（重要）
        - `base_definition` 是 Agent 2 已擷取的條款原始文字，你絕對不能修改、拆解或重新詞述它。
        - 你的工作只有一個：將原始文字轉化為 `formula_template` 和 `python_logic_eval`。
        ## 複雜公式轉譯規則
        若給付邏輯隨條件變動，必須完整保留 IF-ELSE 分支。
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
        ## 倍數與百分比轉譯規則（核心指令）
        保險計算以「1」為基數，不是「1+X」。請嚴格對照以下換算表：

        | 條款寫法              | 公式轉譯         | 說明                              |
        |----------------------|--------------|----------------------------------|
        | xxx 的一倍          | xxx * 1      | 「一倍」= 基準值本身，不是加倍    |
        | xxx 的兩倍          | xxx * 2      | 「兩倍」= 基準值 * 2               |
        | xxx 乘以百分之一  | xxx * 0.01   | 1% = 0.01                        |
        | xxx 乘以百分之兩  | xxx * 0.02   | 2% = 0.02                        |
        | xxx 的 120%         | xxx * 1.2    | 120% = 1.2（不是 xxx + xxx*0.2） |

        重要原則：「xxx 的 N 倍」= xxx * N，不是 xxx * (1+N)。
        """
        super().__init__(
            schema=AGENT_3_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    def parse_logic(
        self, benefit_code: str, related_segments: List[dict], context: PipelineContext
    ) -> dict:
        """
        為「單一」給付項目解析公式邏輯。
        :param benefit_code: 給付項目代碼
        :param related_segments: 該給付項目所關聯的條文片段
        :param context: PipelineContext 包含全域萃取出的名詞定義
        :return: 依照 AGENT_3_SCHEMA 輸出的結果
        """
        global_defs_str = json.dumps(
            context.global_definitions, ensure_ascii=False, indent=2
        )

        content = (
            f"目前正在解析的給付項目代碼: {benefit_code}\n\n"
            f"--- 全域商品名詞定義參考 ---\n"
            f"你在解析邏輯時，請參考以下已經定義好的名詞，以正確理解條款涵義：\n"
            f"{global_defs_str}\n\n"
            f"--- 相關條文內容 ---\n"
            f"{json.dumps(related_segments, ensure_ascii=False, indent=2)}"
        )

        result = self.execute([content])
        if result:
            result["benefit_code"] = benefit_code
        return result or {}
