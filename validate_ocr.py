#!/usr/bin/env python3
"""
阶段 2：OCR 后验证

从阶段 1 的成功结果中随机抽样，验证 OCR 是否真正生成了可搜索文字层。

用法:
    python validate_ocr.py [--db ocr_history.db] [--batch-id N]
                           [--config config.json] [--report validate_report.json]

    # 验证指定批次
    python validate_ocr.py --batch-id 3

    # 验证所有历史成功记录
    python validate_ocr.py

输出:
    控制台摘要 + JSON 详细报告
"""

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import db

try:
    import fitz  # PyMuPDF
except ImportError:
    print("错误：未安装 PyMuPDF。请运行: uv pip install pymupdf")
    sys.exit(1)


def load_success_files(conn, batch_id: int | None = None) -> list[dict]:
    """从进度数据库加载所有成功记录。"""
    return db.get_success_tasks(conn, batch_id)


def detect_text_layer(pdf_path: str, threshold: int = 20) -> dict:
    """检测 PDF 的文字层，返回字符数和判定结果。"""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {"pages": 0, "char_count": 0, "status": "error", "reason": str(e)}

    total_chars = 0
    try:
        page_count = len(doc)
        for page in doc:
            text = str(page.get_text())
            total_chars += len(text.strip())
            if total_chars > threshold:
                break
        doc.close()

        if total_chars > threshold:
            return {
                "pages": page_count,
                "char_count": total_chars,
                "status": "pass",
                "reason": f"可提取 {total_chars} 个字符",
            }
        else:
            return {
                "pages": page_count,
                "char_count": total_chars,
                "status": "fail",
                "reason": f"仅提取 {total_chars} 个字符，OCR 可能未生效",
            }
    except Exception as e:
        doc.close()
        return {"pages": 0, "char_count": 0, "status": "error", "reason": str(e)}


def validate_sample(
    success_files: list[dict], sample_rate: float, min_sample: int, threshold: int
) -> dict:
    """抽样验证 OCR 结果。"""
    total = len(success_files)
    sample_size = max(min_sample, int(total * sample_rate))
    sample_size = min(sample_size, total)

    if total == 0:
        return {
            "total_success": 0,
            "sample_size": 0,
            "pass": 0,
            "fail": 0,
            "error": 0,
            "pass_rate": 0.0,
            "details": [],
        }

    sampled = random.sample(success_files, sample_size)
    random.shuffle(sampled)  # 打乱顺序

    details = []
    pass_count = 0
    fail_count = 0
    error_count = 0

    print(f"\n抽样验证: 从 {total} 个成功文件中抽取 {sample_size} 个")
    print("=" * 60)

    for i, item in enumerate(sampled, 1):
        file_name = Path(item["output"]).name
        result = detect_text_layer(item["output"], threshold)

        status = result["status"]
        if status == "pass":
            pass_count += 1
            label = "✓ 通过"
        elif status == "fail":
            fail_count += 1
            label = "✗ 未通过"
        else:
            error_count += 1
            label = "! 异常"

        print(
            f"[{i}/{sample_size}] {label:<8} | {file_name:<40} | "
            f"{result['char_count']} chars | {result['reason']}"
        )

        details.append(
            {
                "input": item["input"],
                "output": item["output"],
                "pages": result["pages"],
                "char_count": result["char_count"],
                "status": status,
                "reason": result["reason"],
            }
        )

    pass_rate = pass_count / sample_size * 100 if sample_size > 0 else 0

    return {
        "total_success": total,
        "sample_size": sample_size,
        "pass": pass_count,
        "fail": fail_count,
        "error": error_count,
        "pass_rate": round(pass_rate, 2),
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="OCR 后验证：抽样检查 OCR 质量")
    parser.add_argument("--db", "-d", default=db.DEFAULT_DB_PATH, help="进度数据库路径")
    parser.add_argument("--batch-id", "-b", type=int, default=None, help="指定批次 ID 验证（默认验证所有）")
    parser.add_argument("--config", "-c", default="config.json", help="配置文件路径")
    parser.add_argument(
        "--report", "-r", default="", help="输出报告路径（默认自动命名）"
    )
    args = parser.parse_args()

    # 读取配置
    config = {"sample_rate": 0.05, "min_sample": 10, "validation_threshold": 20}
    if Path(args.config).exists():
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f).get("phase2", {})
            config.update(cfg)

    # 连接数据库
    if not Path(args.db).exists():
        print(f"错误: 进度数据库不存在: {args.db}")
        sys.exit(1)

    conn = db.get_connection(args.db)

    # 如果有 batch-id，校验是否存在
    if args.batch_id is not None:
        batch = db.get_batch(conn, args.batch_id)
        if batch is None:
            print(f"错误: 批次 ID {args.batch_id} 不存在")
            # 列出可用批次
            batches = db.list_batches(conn, limit=10)
            if batches:
                print("\n可用批次:")
                for b in batches:
                    print(f"  id={b['id']} | {b['name']} | {b['status']} | {b['created_at']}")
            conn.close()
            sys.exit(1)
        print(f"验证批次: {batch['name']} (id={args.batch_id})")

    success_files = load_success_files(conn, args.batch_id)
    if not success_files:
        scope = f"批次 {args.batch_id}" if args.batch_id else "数据库"
        print(f"{scope} 中无成功记录，无需验证。")
        conn.close()
        sys.exit(0)

    # 执行验证
    report = validate_sample(
        success_files,
        config["sample_rate"],
        config["min_sample"],
        config["validation_threshold"],
    )

    # 输出摘要
    print("=" * 60)
    print("\n验证摘要:")
    print(f"  成功总数: {report['total_success']}")
    print(f"  抽样数:   {report['sample_size']}")
    print(f"  通过:     {report['pass']}")
    print(f"  未通过:   {report['fail']}")
    print(f"  异常:     {report['error']}")
    print(f"  通过率:   {report['pass_rate']:.1f}%")

    if report["pass_rate"] < 80:
        print("\n⚠ 警告: 通过率低于 80%，建议检查 PDF Expert 设置或 AppleScript 兼容性")

    # 保存报告
    report_data = {
        "generated_at": datetime.now().isoformat(),
        "config": config,
        "batch_id": args.batch_id,
        "summary": {
            "total_success": report["total_success"],
            "sample_size": report["sample_size"],
            "pass": report["pass"],
            "fail": report["fail"],
            "error": report["error"],
            "pass_rate": report["pass_rate"],
        },
        "details": report["details"],
    }

    report_path = args.report or f"reports/validate_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"\n验证报告已保存: {report_path}")
    conn.close()


if __name__ == "__main__":
    main()
