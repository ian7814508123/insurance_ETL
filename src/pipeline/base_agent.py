import json
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
import sys
from pathlib import Path

# 把專案根目錄加入 sys.path 以便匯入 config
root_path = Path(__file__).parent.parent.parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

import config

class BaseAgent:
    """所有 Agent 的基礎類別，封裝 LLM 呼叫與基本重試邏輯"""
    def __init__(
        self,
        schema: Dict[str, Any],
        system_instruction: str,
        api_key: str = config.GEMINI_API_KEY,
        model_name: str = config.DEFAULT_MODEL,
        temperature: float = 0.1
    ):
        self.client = genai.Client(api_key=api_key)
        self.schema = schema
        self.system_instruction = system_instruction
        self.model_name = model_name
        self.temperature = temperature

    def execute(self, contents: List[Any], **kwargs) -> Any:
        """
        執行 Agent 呼叫
        :param contents: 可以是字串或 types.Part 的列表
        :param kwargs: 其他傳遞給 GenerateContentConfig 的參數
        :return: 解析後的 JSON 物件 (Dict 或 List)
        """
        # 可以把 system_instruction 加到第一個 contents 的前面
        # 或者如果有支援 system_instruction 參數的話可以傳給 config
        
        full_contents = [f"# Role & System Instructions\n{self.system_instruction}\n\n# Input Data\n"]
        full_contents.extend(contents)

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=full_contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=self.schema,
                temperature=self.temperature,
                **kwargs
            ),
        )

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response: {e}")
            print(f"Raw response: {response.text}")
            return None
