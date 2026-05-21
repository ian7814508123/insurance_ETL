import os
import pathlib
import re
import io
import numpy as np
from typing import Optional, List
import fitz  # PyMuPDF
from PIL import Image

try:
    import pymupdf4llm
except ImportError:
    print("請安裝: pip install pymupdf4llm")

# 延遲載入 PaddleOCR
_paddle_ocr_instance = None


def get_paddle_ocr():
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            print("請安裝 PaddleOCR: pip install paddleocr paddlepaddle")
            return None

        # 嘗試使用最穩定的參數組合
        # 優先嘗試新版參數 (use_textline_orientation) 並嘗試關閉日誌
        try:
            _paddle_ocr_instance = PaddleOCR(
                lang="ch", use_textline_orientation=True, show_log=False
            )
        except ValueError as e:
            # 如果 show_log 或是參數名稱不對，進行備援
            error_msg = str(e)
            if "show_log" in error_msg:
                # 某些版本不支援 show_log 參數
                try:
                    _paddle_ocr_instance = PaddleOCR(
                        lang="ch", use_textline_orientation=True
                    )
                except ValueError:
                    _paddle_ocr_instance = PaddleOCR(
                        lang="ch", use_angle_cls=True, enable_mkldnn=False
                    )
            elif "use_textline_orientation" in error_msg:
                # 回退到舊版參數
                _paddle_ocr_instance = PaddleOCR(
                    lang="ch", use_angle_cls=True, show_log=False, enable_mkldnn=False
                )
            else:
                # 最基本配置
                _paddle_ocr_instance = PaddleOCR(lang="ch")

    return _paddle_ocr_instance


class HybridPDFProcessor:
    """
    進階雙引擎 PDF 處理器：
    1. PyMuPDF (高效率、支援表格、保留結構)
    2. PaddleOCR (備援、解決 Unicode 亂碼、掃描件)
    """

    def __init__(self, output_dir: str = "data/claim_items/markdown"):
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def is_text_garbled(self, text: str) -> bool:
        """
        更嚴格的亂碼偵測邏輯。
        """
        cleaned_text = re.sub(r"\s+", "", text)
        if not cleaned_text:
            return True  # 可能是純圖片

        # 1. 偵測 PUA 區段字元 (保單常見亂碼區)
        pua_chars = len([c for c in cleaned_text if "\ue000" <= c <= "\uf8ff"])
        pua_ratio = pua_chars / len(cleaned_text)
        if pua_ratio > 0.1:
            return True

        # 2. 檢查中文字元佔比 (排除純符號或亂碼英數)
        total_len = len(cleaned_text)
        chinese_chars = len([c for c in cleaned_text if "\u4e00" <= c <= "\u9fff"])
        # 如果有文字但中文字比例極低（在保單這種中文環境下不合理）
        if total_len > 10:
            if chinese_chars / total_len < 0.05:
                return True

        return False

    def clean_markdown(self, md_text: str) -> str:
        """優化 Markdown 內容品質"""
        # 移除樣張、浮水印
        md_text = re.sub(r"樣\s*[張本]", "", md_text)

        # 修正保單條款常見的標題斷行問題
        # 例如: 「第十條： \n 保險責任」 -> 「## 第十條：保險責任」
        md_text = re.sub(
            r"(第[一二三四五六七八九十百]+條[：:\s]*)", r"\n\n## \1", md_text
        )

        # 移除 PDF 轉出的連續重複字元 (常見於表格框線模擬)
        md_text = re.sub(r"_{3,}", "", md_text)
        md_text = re.sub(r"-{5,}", "", md_text)

        # 壓縮過多的換行
        md_text = re.sub(r"\n{4,}", "\n\n\n", md_text)
        return md_text.strip()

    def _process_with_paddle(self, pdf_path: str) -> str:
        """
        使用 PaddleOCR 並優化行排序邏輯，將結果重組為類似 Markdown 的格式。
        """
        print(f"[*] 啟動 PaddleOCR 解析...")
        ocr = get_paddle_ocr()
        doc = fitz.open(pdf_path)
        md_lines = [f"# PDF OCR \n檔案：{pathlib.Path(pdf_path).name}\n"]

        try:
            import cv2
        except ImportError:
            print("[!] 找不到 cv2 (opencv-python)，請安裝以優化 OCR 處理。")
            cv2 = None

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            # 提高 DPI 以增加辨識率
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")

            # 讀取影像並確保為 RGB
            img_pil = Image.open(io.BytesIO(img_data)).convert("RGB")
            img_array = np.array(img_pil)

            if cv2:
                # PaddleOCR 內部通常偏好 BGR (OpenCV 格式)
                img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            else:
                img = img_array

            # OCR 辨識
            result = ocr.ocr(img)
            if not result or not result[0]:
                continue

            md_lines.append(f"--- 第 {page_idx + 1} 頁 ---")

            # 取得所有區塊並進行座標排序
            # PaddleOCR 的結果格式為: [[ [ [x,y],[x,y]... ], (text, score) ], ...]
            boxes = result[0]

            # 智慧分行演算法：根據 Y 座標的中點進行群組
            # 容許 15 像素內的誤差視為同一行 (基於 300 DPI)
            boxes.sort(key=lambda x: x[0][0][1])  # 先按 Y 排序

            current_line_y = -1
            line_buffer = []

            for box in boxes:
                coords = box[0]
                text = box[1][0]
                y_mid = (coords[0][1] + coords[2][1]) / 2

                if current_line_y == -1:
                    current_line_y = y_mid
                    line_buffer.append(box)
                elif abs(y_mid - current_line_y) < 20:  # 同一行的垂直閾值
                    line_buffer.append(box)
                else:
                    # 輸出上一行 (按 X 排序)
                    line_buffer.sort(key=lambda x: x[0][0][0])
                    md_lines.append(" ".join([b[1][0] for b in line_buffer]))
                    # 開始新的一行
                    current_line_y = y_mid
                    line_buffer = [box]

            # 處理最後一列
            if line_buffer:
                line_buffer.sort(key=lambda x: x[0][0][0])
                md_lines.append(" ".join([b[1][0] for b in line_buffer]))

            md_lines.append("")  # 頁面間隔

        doc.close()
        return "\n".join(md_lines)

    def process(self, pdf_path: str) -> str:
        """主入口：自動判定引擎分流"""
        pdf_path_obj = pathlib.Path(pdf_path)
        if not pdf_path_obj.exists():
            print(f"錯誤: 找不到檔案 {pdf_path}")
            return ""

        file_name = pdf_path_obj.stem
        md_file_path = self.output_dir / f"{file_name}.md"

        print(f"[*] 正在分析: {pdf_path_obj.name}")

        needs_ocr = False
        md_content = ""

        try:
            doc = fitz.open(pdf_path)

            # 修復技巧：移除結構樹，防止表格讀取混亂
            cat = doc.pdf_catalog()
            doc.xref_set_key(cat, "StructTreeRoot", "null")

            # 檢測前三頁樣本
            sample_text = ""
            for i in range(min(3, len(doc))):
                sample_text += doc[i].get_text()

            if self.is_text_garbled(sample_text):
                print(f"[!] 偵測到文字編碼異常或掃描件，將使用 OCR 處理。")
                needs_ocr = True

            if not needs_ocr:
                try:
                    print(f"[+] 採用原生 PyMuPDF...")
                    # pymupdf4llm 具備較好的表格保持能力
                    md_content = pymupdf4llm.to_markdown(doc=doc, show_progress=False)
                    md_content = self.clean_markdown(md_content)
                except Exception as e:
                    print(f"[-] 原生模式解析失敗: {e}，切換備援。")
                    needs_ocr = True

            doc.close()

        except Exception as e:
            print(f"[!] PDF 開啟失敗: {e}")
            needs_ocr = True

        # 執行 OCR 備援
        if needs_ocr:
            md_content = self._process_with_paddle(pdf_path)

        # 最終儲存
        with open(md_file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"[OK] 轉換完成 -> {md_file_path}")
        return md_content


if __name__ == "__main__":
    import sys

    # 使用範例: python script.py my_policy.pdf
    input_file = sys.argv[1] if len(sys.argv) > 1 else None
    if input_file:
        processor = HybridPDFProcessor()
        processor.process(input_file)
    else:
        print("請輸入 PDF 檔案路徑。")
