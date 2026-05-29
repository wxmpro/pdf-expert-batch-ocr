#!/usr/bin/env python3
"""
阶段 0：PDF 预扫描检测
检测哪些 PDF 已有文字层，避免对可搜索 PDF 重复 OCR。

用法:
    python scan_pdfs.py <input_dir> [--recursive] [--threshold 20] [--output scan_result.json]

输出 JSON 结构:
    {
        "scan_info": { "directory": "...", "recursive": true, "threshold": 20, ... },
        "statistics": { "total": 350, "need_ocr": 230, "has_text": 120, "error": 0 },
        "files": [
            {"path": "...", "status": "need_ocr", "pages": 10, "char_count": 5, "reason": "..."},
            ...
        ]
    }
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    import fitz  # PyMuPDF
except ImportError:
    print("错误：未安装 PyMuPDF。请运行: uv pip install pymupdf")
    sys.exit(1)


def detect_hand_drawn(doc) -> bool:
    """检测是否为手绘版 PDF（大量矢量路径、极少文字和图片）。"""
    try:
        total_drawings = 0
        total_images = 0
        for page in doc:
            try:
                drawings = page.get_drawings()
                total_drawings += len(drawings)
            except Exception as e:
                print(f"  警告: 提取绘图时出错: {e}")
            try:
                images = page.get_images()
                total_images += len(images)
            except Exception as e:
                print(f"  警告: 提取图片时出错: {e}")
        # 手绘版特征：路径极多（>50）且图片极少（<3）
        return total_drawings > 50 and total_images < 3
    except Exception as e:
        print(f"  警告: 检测手绘版时出错: {e}")
        return False


def detect_text_layer(pdf_path: str, threshold: int = 20) -> dict:
    """检测单个 PDF 是否已有文字层，并排除手绘版。

    返回:
        dict: {
            "status": "need_ocr" | "has_text" | "hand_drawn" | "error",
            "pages": int,
            "char_count": int,
            "reason": str
        }
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {
            "status": "error",
            "pages": 0,
            "char_count": 0,
            "reason": f"无法打开文件: {e}",
        }

    page_count = 0
    try:
        total_chars = 0
        page_count = len(doc)

        for page in doc:
            text = str(page.get_text())
            total_chars += len(text.strip())
            # 若累计已超阈值，提前结束
            if total_chars > threshold:
                break

        # 字符数不足阈值时，进一步检测是否为手绘版
        if total_chars <= threshold:
            if detect_hand_drawn(doc):
                doc.close()
                return {
                    "status": "hand_drawn",
                    "pages": page_count,
                    "char_count": total_chars,
                    "reason": f"仅提取 {total_chars} 个字符，但检测到大量矢量路径，判定为手绘版",
                }

        doc.close()

        if total_chars > threshold:
            return {
                "status": "has_text",
                "pages": page_count,
                "char_count": total_chars,
                "reason": f"已提取 {total_chars} 个字符（阈值: {threshold}）",
            }
        else:
            return {
                "status": "need_ocr",
                "pages": page_count,
                "char_count": total_chars,
                "reason": f"仅提取 {total_chars} 个字符（阈值: {threshold}），判定为扫描版",
            }

    except Exception as e:
        doc.close()
        return {
            "status": "error",
            "pages": page_count,
            "char_count": 0,
            "reason": f"提取文字时出错: {e}",
        }


def find_pdf_files(directory: str, recursive: bool = False) -> list[str]:
    """查找目录下的所有 PDF 文件。"""
    root = Path(directory).resolve()
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")

    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(str(p) for p in root.glob(pattern) if p.is_file())


def main():
    parser = argparse.ArgumentParser(
        description="PDF 预扫描检测：区分需 OCR 与已有文字层的文件"
    )
    parser.add_argument("input_dir", help="输入目录路径")
    parser.add_argument(
        "--recursive", "-r", action="store_true", help="递归扫描子目录"
    )
    parser.add_argument(
        "--threshold", "-t", type=int, default=20,
        help="文字层判定阈值（字符数），默认 20"
    )
    parser.add_argument(
        "--output", "-o", default="scan_result.json",
        help="输出 JSON 文件路径，默认 scan_result.json"
    )
    args = parser.parse_args()

    # 查找 PDF 文件
    try:
        pdf_files = find_pdf_files(args.input_dir, args.recursive)
    except FileNotFoundError as e:
        print(f"错误: {e}")
        sys.exit(1)

    total = len(pdf_files)
    if total == 0:
        print(f"未在 '{args.input_dir}' 下找到 PDF 文件")
        sys.exit(0)

    print(f"共找到 {total} 个 PDF 文件，开始检测文字层...")
    print(f"判定阈值: {args.threshold} 个字符")
    print("-" * 60)

    results = []
    stats = {"total": total, "need_ocr": 0, "has_text": 0, "hand_drawn": 0, "error": 0}

    for i, pdf_path in enumerate(pdf_files, 1):
        # 显示进度
        progress = f"[{i}/{total}]"
        result = detect_text_layer(pdf_path, args.threshold)

        stats[result["status"]] += 1

        # 相对路径（让输出更简洁）
        rel_path = Path(pdf_path).name
        status_label = {
            "need_ocr": "需 OCR",
            "has_text": "已有文字层",
            "hand_drawn": "手绘版",
            "error": "异常",
        }[result["status"]]

        print(f"{progress} {status_label:<8} | {rel_path:<40} | {result['reason']}")

        results.append({
            "path": pdf_path,
            "status": result["status"],
            "pages": result["pages"],
            "char_count": result["char_count"],
            "reason": result["reason"],
        })

    # 生成报告
    report = {
        "scan_info": {
            "directory": str(Path(args.input_dir).resolve()),
            "recursive": args.recursive,
            "threshold": args.threshold,
            "timestamp": datetime.now().isoformat(),
        },
        "statistics": stats,
        "files": results,
    }

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("-" * 60)
    print(f"扫描完成。统计:")
    print(f"  总计: {total}")
    print(f"  需 OCR:     {stats['need_ocr']} ({stats['need_ocr']/total*100:.1f}%)")
    print(f"  已有文字层: {stats['has_text']} ({stats['has_text']/total*100:.1f}%)")
    print(f"  手绘版:     {stats['hand_drawn']} ({stats['hand_drawn']/total*100:.1f}%)")
    print(f"  异常:       {stats['error']} ({stats['error']/total*100:.1f}%)")
    print(f"\n报告已保存: {output_path.resolve()}")

    # 生成待处理清单（方便下一阶段直接读取）
    need_ocr_list = [f for f in results if f["status"] == "need_ocr"]
    ocr_list_path = output_path.with_stem("ocr_queue")
    with open(ocr_list_path, "w", encoding="utf-8") as f:
        json.dump({"queue": need_ocr_list, "count": len(need_ocr_list)}, f, ensure_ascii=False, indent=2)
    print(f"待处理清单:  {ocr_list_path.resolve()}")


if __name__ == "__main__":
    main()
