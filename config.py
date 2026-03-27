import os
from dotenv import load_dotenv

load_dotenv()

# --- 應用程式配置 ---

# Gemini API Key - 請妥善保管，不要上傳至公開路徑

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 預設模型設定
DEFAULT_MODEL = "gemini-2.5-flash"
