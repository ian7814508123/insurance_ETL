import json
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
import sys
from pathlib import Path
import asyncio

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
        """同步執行 Agent 呼叫"""
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
            return None

    async def async_execute(self, contents: List[Any], **kwargs) -> Any:
        """
        非同步執行 Agent 呼叫 (使用 google-genai 內建的 aio 支援)
        """
        full_contents = [f"# Role & System Instructions\n{self.system_instruction}\n\n# Input Data\n"]
        full_contents.extend(contents)

        # 使用 aio client 進行非同步呼叫
        response = await self.client.aio.models.generate_content(
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
            return None
