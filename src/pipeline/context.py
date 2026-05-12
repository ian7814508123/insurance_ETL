from typing import Dict, List, Any

class PipelineContext:
    """
    Pipeline 的資料白板 (Data Whiteboard/Shared Context)。
    用於在各個 Agent 之間傳遞全局狀態，避免上下文斷裂導致的參數命名不一。
    """
    def __init__(self):
        # 存放由 DefinitionExtractor 萃取出來的名詞定義
        self.global_definitions: List[Dict[str, Any]] = []
        
        # 存放已註冊的標準變數名稱
        # 格式: { "中文名詞": "英文變數名 (例如: HOSPITAL_DAILY_AMOUNT)" }
        self.standard_parameters: Dict[str, str] = {}

        # 存放基礎理賠項目定義，供 Agent 2 參考對齊
        self.base_claim_items: List[Dict[str, Any]] = []

    def add_standard_parameter(self, zh_name: str, en_name: str) -> None:
        """
        註冊一個標準變數，如果中文名稱已經存在，則不會覆寫，
        以確保最先註冊的名稱成為唯一標準。
        """
        if zh_name not in self.standard_parameters:
            self.standard_parameters[zh_name] = en_name

    def register_parameters_from_agent4(self, params_list: List[Dict[str, Any]]) -> None:
        """
        批次從 Agent 4 的輸出結果中註冊新變數。
        預期 Agent 4 輸出的 params 包含 'parameter_name' 與 'parameter_desc' (或類似能代表中文意義的欄位)。
        """
        for param in params_list:
            en_name = param.get("parameter_name")
            zh_name = param.get("description") or param.get("parameter_desc") or ""
            if en_name and zh_name:
                self.add_standard_parameter(zh_name, en_name)
