#!/usr/bin/env python3
"""
阶段 1：批量 OCR 调度器

Python 管理进度数据库 + 调用 AppleScript 驱动 PDF Expert UI。
支持断点续传、错误隔离、内存重启、优雅中断。

用法:
    python batch_ocr.py --queue ocr_queue.json --output-dir ./output
                        [--db ocr_history.db] [--config config.json]
                        [--batch-name "描述名称"]

流程:
    1. 读取阶段 0 生成的 ocr_queue.json
    2. 初始化 SQLite 进度数据库（已存在则读取已有进度）
    3. 逐个处理 pending 文件：复制副本 → AppleScript OCR → Save → 记录结果
    4. 每 N 个文件重启 PDF Expert 以释放内存
    5. 支持 Ctrl+C 安全中断，下次运行自动续传
"""

import argparse
import json
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import db

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False


# ── 全局状态 ──────────────────────────────────────────────
_conn = None          # SQLite 连接，用于信号处理中保存进度
_batch_id = None      # 当前批次 ID，用于信号处理
_conn_lock = threading.Lock()  # 保护 _conn 并发访问


# ── 路径安全校验 ─────────────────────────────────────────
def validate_path(path_str: str, must_exist: bool = False, must_be_pdf: bool = False) -> Path:
    """校验路径安全性，防止路径遍历攻击。

    规则:
        1. 禁止包含 '..' 的相对路径穿越
        2. 必须指向文件系统内的实际路径
        3. PDF 文件必须以 .pdf 结尾
    """
    p = Path(path_str)

    # 检查路径遍历
    resolved = p.resolve()
    if ".." in str(p):
        raise ValueError(f"路径包含非法的 '..' 穿越: {path_str}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"路径不存在: {resolved}")

    if must_be_pdf:
        if not resolved.is_file():
            raise ValueError(f"不是文件: {resolved}")
        if resolved.suffix.lower() != ".pdf":
            raise ValueError(f"不是 PDF 文件: {resolved}")

    return resolved


def validate_queue_paths(queue_data: dict) -> list[dict]:
    """校验 ocr_queue.json 中的路径，过滤非法条目。"""
    valid = []
    for item in queue_data.get("queue", []):
        path_str = item.get("path", "")
        try:
            p = validate_path(path_str, must_exist=True, must_be_pdf=True)
            item["path"] = str(p)
            valid.append(item)
        except (ValueError, FileNotFoundError) as e:
            print(f"  警告: 跳过非法路径: {e}")
    return valid


# ── 数据库操作 ────────────────────────────────────────────
def load_queue(conn: sqlite3.Connection, batch_id: int, queue_file: str,
               output_dir: str, config: dict) -> tuple[int, int]:
    """从阶段 0 的队列文件加载待处理文件，复用已有成功记录。

    返回: (新插入任务数, 复用已有成功记录数)
    """
    suffix = config.get("output_suffix", "_OCR")

    with open(queue_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 校验队列中的路径
    valid_items = validate_queue_paths(data)

    inserted = 0
    reused = 0
    for item in valid_items:
        input_path = item["path"]
        file_stem = Path(input_path).stem
        file_name = f"{file_stem}{suffix}.pdf"
        output_path = str(Path(output_dir) / file_name)

        # 检查该文件是否已有成功的 OCR 记录（任意批次）
        existing = db.has_successful_ocr(conn, input_path)
        if existing:
            # 复用已有结果：直接复制输出文件（如果存在）
            src_output = existing["output_path"]
            if Path(src_output).exists():
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_output, output_path)
                print(f"  ↻ 复用已有 OCR 结果: {file_name}")
                reused += 1

        # 确保文件记录在 files 表中
        file_id = db.ensure_file(conn, input_path)

        # 创建任务（如果已存在则忽略，保持已有状态）
        task_id = db.create_task(conn, file_id, batch_id, output_path)
        # 如果任务是刚创建的，计数
        cursor = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        row = cursor.fetchone()
        if row and row[0] == "pending":
            inserted += 1

    conn.commit()
    return inserted, reused


# ── PDF Expert 操作 ──────────────────────────────────────
def restart_pdf_expert() -> None:
    """退出并重启 PDF Expert 以释放内存。"""
    print("  → 重启 PDF Expert 释放内存...")
    subprocess.run(
        ["osascript", "-e", 'tell application "PDF Expert" to quit'],
        capture_output=True,
    )
    time.sleep(2)
    subprocess.run(["open", "-a", "PDF Expert"], capture_output=True)
    time.sleep(3)
    print("  → PDF Expert 已重启")


# ── AppleScript 调用 ─────────────────────────────────────
def run_applescript(scpt_path: str, file_path: str, ocr_lang: str, _timeout: int) -> dict:
    """调用 AppleScript 对单个文件执行 OCR。

    注意：不设置 Python 层面的 timeout，由 AppleScript 内部检测 OCR 完成状态。
    返回: {"status": "success"|"failed"|"timeout", "stdout": str, "stderr": str}
    """
    try:
        args = ["osascript", scpt_path, file_path, "keep"]
        if ocr_lang:
            args.append(ocr_lang)

        # 不设置 timeout，让 AppleScript 自己检测 OCR 完成
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
        )
        # 打印完整 AppleScript 输出以便调试
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                print(f"    [AS] {line}")
        if result.returncode == 0:
            stdout = result.stdout.strip().splitlines()
            last_line = stdout[-1] if stdout else ""
            if last_line == "success":
                return {"status": "success", "stdout": last_line, "stderr": ""}
            else:
                return {"status": "failed", "stdout": last_line, "stderr": "AppleScript 返回非 success"}
        else:
            return {"status": "failed", "stdout": result.stdout, "stderr": result.stderr}
    except Exception as e:
        return {"status": "failed", "stdout": "", "stderr": str(e)}


# ── OCR 后验证 ────────────────────────────────────────────
def validate_ocr_output(pdf_path: str, threshold: int = 20) -> dict:
    """验证 OCR 输出文件是否真正获得了文字层。

    返回: {"ok": bool, "char_count": int, "pages": int}
    """
    if not _HAS_FITZ:
        return {"ok": True, "char_count": -1, "pages": -1, "reason": "未安装 PyMuPDF，跳过验证"}

    try:
        doc = fitz.open(pdf_path)  # type: ignore[possiblyUnbound]
        total_chars = 0
        page_count = len(doc)
        for page in doc:
            total_chars += len(str(page.get_text()).strip())
            if total_chars > threshold:
                break
        doc.close()

        if total_chars > threshold:
            return {"ok": True, "char_count": total_chars, "pages": page_count, "reason": f"已验证 {total_chars} 个字符"}
        else:
            return {
                "ok": False,
                "char_count": total_chars,
                "pages": page_count,
                "reason": f"OCR 后仅提取 {total_chars} 个字符，PDF Expert 无法识别此文件编码",
            }
    except Exception as e:
        return {"ok": False, "char_count": 0, "pages": 0, "reason": f"验证失败: {e}"}


# ── 文件复制 ──────────────────────────────────────────────
def copy_to_output(input_path: str, output_path: str) -> None:
    """复制原文件到输出目录，始终覆盖已有副本。"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, output_path)


# ── 信号处理 ──────────────────────────────────────────────
def signal_handler(signum, _) -> None:
    """Ctrl+C / SIGTERM 安全退出。"""
    sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    print(f"\n\n收到 {sig_name}，正在保存进度...")
    with _conn_lock:
        if _conn is not None:
            reset_count = db.reset_processing_tasks(_conn, _batch_id)
            if reset_count:
                print(f"  已重置 {reset_count} 个处理中任务为 pending")
            _conn.close()
    print("进度已保存。下次运行会自动从中断点续传。")
    sys.exit(0)


# ── 主循环 ────────────────────────────────────────────────
def process_loop(conn: sqlite3.Connection, batch_id: int, config: dict,
                 scpt_path: str) -> None:
    """主处理循环。"""
    timeout = config.get("single_timeout_seconds", 300)
    restart_interval = config.get("restart_interval", 10)
    delay_between = config.get("delay_between_files", 0.5)
    ocr_lang = config.get("ocr_language", "")

    stats = db.get_task_stats(conn, batch_id)
    total = stats["total"]
    processed = stats["success"] + stats["failed"] + stats["timeout"]
    count_since_restart = processed % restart_interval

    print(f"\n当前进度: 成功 {stats['success']} | 失败 {stats['failed']} | "
          f"超时 {stats['timeout']} | 剩余 {stats['pending']}")
    print(f"共 {total} 个文件，已完成 {processed}/{total}")
    print("=" * 60)

    while True:
        row = db.get_next_pending_task(conn, batch_id)
        if row is None:
            print("\n所有文件处理完毕。")
            break

        task_id = row["id"]
        input_path = row["input_path"]
        output_path = row["output_path"]
        file_name = Path(input_path).name

        # 复制副本（如果不存在）
        try:
            copy_to_output(input_path, output_path)
        except Exception as e:
            print(f"[{processed + 1}/{total}] 复制失败 | {file_name} | {e}")
            db.mark_task_done(conn, task_id, "failed", f"复制失败: {e}")
            processed += 1
            continue

        # 标记为处理中
        db.mark_task_processing(conn, task_id)
        print(f"[{processed + 1}/{total}] 处理中... | {file_name}")

        # 调用 AppleScript
        result = run_applescript(scpt_path, output_path, ocr_lang, timeout)

        # 记录结果
        if result["status"] == "success":
            # OCR 后验证：检查输出文件是否真正获得文字层
            validation = validate_ocr_output(output_path)
            if validation["ok"]:
                db.mark_task_done(conn, task_id, "success")
                print(f"  ✓ 成功 | {validation['reason']}")
            else:
                db.mark_task_done(conn, task_id, "failed", validation["reason"])
                print(f"  ✗ OCR 无效: {validation['reason']}")
        else:
            db.mark_task_done(conn, task_id, result["status"],
                              result.get("stderr", ""))
            print(f"  ✗ {result['status']}: {result.get('stderr', '')[:80]}")

        processed += 1
        count_since_restart += 1

        # 间隔重启
        if restart_interval > 0 and count_since_restart >= restart_interval:
            restart_pdf_expert()
            count_since_restart = 0

        time.sleep(delay_between)


# ── 报告生成 ──────────────────────────────────────────────
def generate_report(conn: sqlite3.Connection, batch_id: int,
                    report_path: str) -> None:
    """生成最终报告。"""
    stats = db.get_task_stats(conn, batch_id)
    details = db.get_all_tasks_with_details(conn, batch_id)

    # 获取批次信息
    batch = db.get_batch(conn, batch_id)
    batch_name = batch["name"] if batch else "unknown"

    report = {
        "generated_at": datetime.now().isoformat(),
        "batch_id": batch_id,
        "batch_name": batch_name,
        "statistics": stats,
        "details": details,
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存: {report_path}")
    if stats["total"] > 0:
        print(f"  总计: {stats['total']}")
        print(f"  成功: {stats['success']} ({stats['success']/stats['total']*100:.1f}%)")
        print(f"  失败: {stats['failed']}")
        print(f"  超时: {stats['timeout']}")
    else:
        print("  该批次暂无任务记录")


# ── 入口 ──────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="PDF Expert 批量 OCR 调度器")
    parser.add_argument("--queue", "-q", required=True, help="阶段 0 生成的 ocr_queue.json")
    parser.add_argument("--output-dir", "-o", required=True, help="OCR 输出目录")
    parser.add_argument("--db", "-d", default=db.DEFAULT_DB_PATH, help="进度数据库路径")
    parser.add_argument("--config", "-c", default="config.json", help="配置文件路径")
    parser.add_argument("--scpt", "-s", default="pdfexpert_ocr.scpt", help="AppleScript 路径")
    parser.add_argument("--batch-name", "-n", default="", help="批次名称（用于区分不同运行）")
    args = parser.parse_args()

    # ── 路径安全校验 ──────────────────────────────────────
    try:
        queue_path = validate_path(args.queue, must_exist=True)
        output_dir = validate_path(args.output_dir)
        db_path = validate_path(args.db)
        config_path = validate_path(args.config)
        scpt_path = validate_path(args.scpt, must_exist=True)
    except (ValueError, FileNotFoundError) as e:
        print(f"路径校验失败: {e}")
        sys.exit(1)

    # 确保输出目录可写
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"无法创建输出目录: {e}")
        sys.exit(1)

    # 读取配置
    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f).get("phase1", {})
        except json.JSONDecodeError as e:
            print(f"配置文件解析失败: {e}")
            sys.exit(1)

    # 注册信号处理
    global _conn, _batch_id
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 初始化数据库
    _conn = db.get_connection(str(db_path))

    # 生成批次名称（如果未提供）
    batch_name = args.batch_name or f"batch_{datetime.now():%Y%m%d_%H%M%S}"

    # 创建批次记录
    source_dir = str(Path(args.queue).parent.resolve())
    _batch_id = db.create_batch(
        _conn, batch_name, source_dir, str(output_dir), config
    )
    print(f"创建批次: {batch_name} (id={_batch_id})")

    # 加载队列
    inserted, reused = load_queue(_conn, _batch_id, str(queue_path),
                                  str(output_dir), config)
    if inserted > 0:
        print(f"新加载 {inserted} 个待处理文件到队列")
    if reused > 0:
        print(f"复用 {reused} 个已有 OCR 结果")

    # 更新批次总文件数
    stats = db.get_task_stats(_conn, _batch_id)
    db.update_batch_status(_conn, _batch_id, "running", stats["total"])

    # 启动 PDF Expert（如果未运行）
    print("启动 PDF Expert...")
    subprocess.run(["open", "-a", "PDF Expert"], capture_output=True)
    time.sleep(3)

    try:
        process_loop(_conn, _batch_id, config, str(scpt_path))
        db.update_batch_status(_conn, _batch_id, "completed")
    except Exception as e:
        db.update_batch_status(_conn, _batch_id, "interrupted")
        raise
    finally:
        # 生成报告
        report_path = Path("reports") / f"batch_ocr_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report_path.parent.mkdir(exist_ok=True)
        generate_report(_conn, _batch_id, str(report_path))
        _conn.close()
        # 关闭 PDF Expert（带超时和强制退出后备）
        print("\n关闭 PDF Expert...")
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "PDF Expert" to quit'],
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            print("  PDF Expert 未响应，强制退出...")
            subprocess.run(
                ["osascript", "-e", 'tell application "PDF Expert" to quit saving no'],
                capture_output=True,
                timeout=5,
            )
        # 等待 PDF Expert 完全退出
        time.sleep(2)


if __name__ == "__main__":
    main()
