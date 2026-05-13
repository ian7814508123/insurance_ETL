import os
import pathlib
import re
from typing import Optional
import fitz  # PyMuPDF

try:
    import pymupdf4llm
except ImportError:
    pass

# 延遲載入 PaddleOCR，只有在真正需要 AI 救援時才初始化，節省記憶體
_paddle_ocr_instance = None


def get_paddle_ocr():
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        from paddleocr import PaddleOCR

        # lang="structure" 或 "ch" 搭配繁體支援
        # use_angle_cls=True 自動修正歪斜的保單頁面
        _paddle_ocr_instance = PaddleOCR(lang="ch", use_angle_cls=True, show_log=False)
    return _paddle_ocr_instance


class HybridPDFProcessor:
    """
    雙引擎 PDF 前處理器
    - 高速模式：PyMuPDF (處理數位原生、無損 PDF)
    - 備援模式：PaddleOCR (處理繁體中文亂碼、掃描件、拍照件)
    """

    def __init__(self, output_dir: str = "data/claim_items/markdown"):
        self.output_dir = pathlib.Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def is_text_garbled(self, text: str) -> bool:
        """
        診斷提取出的文字是否為亂碼或需要 OCR。
        """
        cleaned_text = re.sub(r"\s+", "", text)
        if not cleaned_text:
            return True  # 空白文本，代表可能是純圖片掃描件

        # 1. 檢查 PUA 區段字元 (\uE000-\uF8FF)
        pua_chars = [c for c in cleaned_text if "\ue000" <= c <= "\uf8ff"]
        pua_ratio = len(pua_chars) / len(cleaned_text) if cleaned_text else 0
        if pua_ratio > 0.15:
            return True

        # 2. 檢查中文字元比例，排除純英數干擾
        total_len = len(cleaned_text)
        english_or_digits = len(re.findall(r"[a-zA-Z0-9[:punct:]]", cleaned_text))

        if total_len - english_or_digits > 20:
            chinese_chars = [c for c in cleaned_text if "\u4e00" <= c <= "\u9fff"]
            chinese_ratio = len(chinese_chars) / (total_len - english_or_digits)
            if chinese_ratio < 0.10:
                return True

        return False

    def clean_markdown(self, md_text: str) -> str:
        """清理 Markdown 中的雜訊"""
        # 移除樣張、樣本等常見浮水印雜訊
        md_text = re.sub(r"樣\s*張", "", md_text)
        md_text = re.sub(r"樣\s*本", "", md_text)
        
        # 處理散落在文字間的單個雜訊字元 (常見於保單側邊)
        md_text = re.sub(r"~~樣~~", "", md_text)
        md_text = re.sub(r"~~本~~", "", md_text)
        
        # 修正「第XX條」被併入上一行末尾的問題
        # 尋找：(句號/括號/引號) + (空格) + 第XX條
        # 替換為：換行 + ## 第XX條
        md_text = re.sub(r"([。：」）])\s*(第[一二三四五六七八九十]+條：)", r"\1\n\n## \2", md_text)
        
        # 移除多餘換行
        md_text = re.sub(r"\n{3,}", "\n\n", md_text)
        return md_text

    def _process_with_paddle(self, pdf_path: str) -> str:
        """
        當 PDF 內容是中文亂碼時，使用 PaddleOCR 逐頁解析並重組為 Markdown。
        """
        print(f"啟動 PaddleOCR 解析...")
        ocr = get_paddle_ocr()
        doc = fitz.open(pdf_path)
        md_lines = [f"# PDF 解析結果 \n檔案：{pathlib.Path(pdf_path).name}\n"]

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            md_lines.append(f"## 第 {page_idx + 1} 頁\n")

            # 將 PDF 頁面渲染為高解析度圖片 (dpi=200 兼顧速度與精準度)
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")

            # 送入 PaddleOCR 辨識
            result = ocr.ocr(img_bytes, cls=True)

            if not result or not result[0]:
                md_lines.append("*(本頁無可辨識文字)*\n")
                continue

            # PaddleOCR 的結果預設為自由幾何框
            # 依據 Y 軸座標（由上到下）與 X 軸座標（由左到右）對文字框進行排序重組
            boxes = result[0]
            # 智慧分行：Y 軸相差在 10 像素以內的文字視為同一行
            boxes.sort(key=lambda x: (x[0][0][1], x[0][0][0]))

            current_y = -1
            line_text = ""

            for box in boxes:
                text_info = box[1]
                text = text_info[0]  # 辨識出的文字
                confidence = text_info[1]  # 信心度

                if confidence < 0.5:  # 過濾掉極度模糊的雜訊
                    continue

                box_coords = box[0]
                y_top = box_coords[0][1]

                # 如果換行了 (Y 軸落差大於 12 像素)
                if current_y == -1:
                    current_y = y_top
                    line_text = text
                elif abs(y_top - current_y) > 12:
                    md_lines.append(line_text)
                    current_y = y_top
                    line_text = text
                else:
                    # 同一行，用空格或逗號串接
                    line_text += " " + text

            if line_text:
                md_lines.append(line_text)

            md_lines.append("\n")  # 頁面結束換行

        doc.close()
        return "\n".join(md_lines)

    def process(self, pdf_path: str) -> str:
        """智慧雙軌解析主邏輯"""
        pdf_path_obj = pathlib.Path(pdf_path)
        file_name = pdf_path_obj.stem
        md_file_path = self.output_dir / f"{file_name}.md"

        print(f"[*] 正在處理 PDF: {pdf_path}")

        # 1. 讀取 PDF 並套用修復邏輯 (無視損毀的標籤結構)
        needs_ai_ocr = False
        doc = None
        try:
            doc = fitz.open(pdf_path)
            
            # --- 套用 StructTreeRoot 修復 (解決分欄讀取錯誤) ---
            cat = doc.pdf_catalog()
            doc.xref_set_key(cat, "StructTreeRoot", "null")
            # ----------------------------------------------

            if len(doc) > 0:
                check_pages = min(3, len(doc))
                sample_text = "".join([doc[i].get_text() for i in range(check_pages)])
                if self.is_text_garbled(sample_text):
                    print(f"[!] 偵測到 Unicode 損毀或純圖片，切換 備援模式。")
                    needs_ai_ocr = True
        except Exception as e:
            print(f"[!] 無法預讀 PDF: {e}")
            needs_ai_ocr = True

        # 2. 根據偵測結果分流執行
        md_content = ""
        if not needs_ai_ocr and doc:
            try:
                # 優先使用高速原生 Markdown 轉換
                print(f"採用 PyMuPDF (Layout Aware) ...")
                # 傳遞已套用修復的 doc 物件
                md_content = pymupdf4llm.to_markdown(
                    doc=doc, 
                    show_progress=False
                )
                md_content = self.clean_markdown(md_content)
            except Exception as e:
                print(f"[!] 原生解析失敗，切換至 備援: {e}")
                needs_ai_ocr = True

        # 如果被標記需要 AI OCR，或是原生解析中途崩潰
        if needs_ai_ocr:
            md_content = self._process_with_paddle(pdf_path)

        if doc:
            doc.close()

        # 3. 儲存 Markdown 結果
        with open(md_file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"[+] 轉換完成！已儲存至: {md_file_path}\n" + "-" * 40)
        return md_content


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        processor = HybridPDFProcessor()
        processor.process(sys.argv[1])
    else:
        print("請提供 PDF 路徑進行測試。")
