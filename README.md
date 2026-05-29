# PDF Expert Batch OCR

Batch OCR tool for scanned PDFs using [PDF Expert](https://pdfexpert.com/) on macOS. Converts image-based PDFs into searchable documents while **never modifying the original files**.

通过 macOS 上的 [PDF Expert](https://pdfexpert.com/) 批量 OCR 扫描版 PDF，生成可搜索的新文件。**原文件始终不受影响**。

---

## Features / 功能特性

- **Three-phase pipeline** / 三阶段流水线：Pre-scan → Batch OCR → Post-validation
- **Breakpoint resume** / 断点续传：SQLite-based progress tracking, safe to interrupt and resume
- **Error isolation** / 错误隔离：Single file failure does not abort the entire batch
- **Memory management** / 内存管理：Auto-restart PDF Expert every N files to prevent memory bloat
- **Safe interrupt** / 安全中断：Ctrl+C saves progress before exit
- **Large file support** / 大文件支持：Automatic splitting for PDFs > 100 pages
- **Text layer verification** / 文字层验证：PyMuPDF validates OCR actually produced searchable text

---

## Requirements / 环境要求

| Component | Version |
|-----------|---------|
| macOS | 14+ (AppleScript UI Scripting required) |
| Python | 3.11+ |
| PDF Expert | 3.x (Chinese UI / 中文界面) |
| PyMuPDF | >= 1.23.0 |

**Important**: PDF Expert must be the default PDF application and must have OCR language configured (default: Chinese / 中文).

---

## Installation / 安装

```bash
# Install Python dependency
pip install -r requirements.txt

# Or using uv
uv pip install -r requirements.txt
```

---

## Usage / 使用流程

### Phase 0: Pre-scan / 预扫描

Detect which PDFs already have a text layer to avoid redundant OCR.

```bash
python scan_pdfs.py /path/to/your/pdfs --output scan_result.json
```

**Output:**
- `scan_result.json` — Full scan report
- `ocr_queue.json` — Queue file for Phase 1 (auto-generated)

**Status codes:**
| Status | Meaning |
|--------|---------|
| `need_ocr` | Scanned PDF, requires OCR |
| `has_text` | Already searchable, skipped |
| `hand_drawn` | Hand-drawn / vector-heavy, skipped |
| `error` | Failed to open/read |

---

### Phase 1: Batch OCR / 批量 OCR

Drive PDF Expert via AppleScript to process files one by one.

```bash
python batch_ocr.py \
  --queue ocr_queue.json \
  --output-dir ./output \
  --db progress.db \
  --scpt pdfexpert_ocr.scpt
```

**Behavior:**
- Creates a copy (`*_OCR.pdf`) in `output-dir` before processing
- Original files are **never touched**
- Automatically restarts PDF Expert every 10 files (configurable)
- Progress saved to SQLite database — re-run to resume from interruption

**Single file mode** (for testing):
```bash
osascript pdfexpert_ocr.scpt /path/to/file.pdf
```

---

### Phase 2: Post-validation / 后验证

Random sampling to verify OCR quality.

```bash
python validate_ocr.py --db progress.db
```

---

## Large File Handling / 大文件处理

For PDFs > 100 pages, split into chunks before OCR to avoid memory/timeouts:

```python
import fitz
from pathlib import Path

src = "large_400pages.pdf"
out_dir = Path("./chunks")
out_dir.mkdir(exist_ok=True)

doc = fitz.open(src)
chunk_size = 50

for start in range(0, len(doc), chunk_size):
    end = min(start + chunk_size, len(doc))
    chunk = fitz.open()
    chunk.insert_pdf(doc, from_page=start, to_page=end - 1)
    chunk.save(out_dir / f"part_{start+1:03d}-{end:03d}.pdf")
    chunk.close()

doc.close()
```

Then run Phase 1 on the chunks and merge afterward.

---

## Configuration / 配置

Edit `config.json`:

```json
{
  "phase0": {
    "threshold": 20,
    "recursive": false
  },
  "phase1": {
    "output_suffix": "_OCR",
    "ocr_language": "中文",
    "restart_interval": 10,
    "single_timeout_seconds": 300,
    "delay_between_files": 0.5
  },
  "phase2": {
    "sample_rate": 0.05,
    "min_sample": 10,
    "validation_threshold": 20
  }
}
```

| Key | Description | Default |
|-----|-------------|---------|
| `phase0.threshold` | Character threshold for text layer detection | 20 |
| `phase0.recursive` | Recursively scan subdirectories | false |
| `phase1.output_suffix` | Suffix for OCR output files | `_OCR` |
| `phase1.ocr_language` | OCR language setting in PDF Expert | `中文` |
| `phase1.restart_interval` | Restart PDF Expert every N files | 10 |
| `phase1.single_timeout_seconds` | Per-file timeout | 300 |
| `phase2.sample_rate` | Validation sampling rate | 0.05 |
| `phase2.min_sample` | Minimum validation samples | 10 |

---

## Project Structure / 项目结构

```
.
├── scan_pdfs.py            # Phase 0: Pre-scan detector
├── batch_ocr.py            # Phase 1: Batch OCR scheduler
├── validate_ocr.py         # Phase 2: Post-validation sampler
├── pdfexpert_ocr.scpt      # AppleScript UI driver for PDF Expert
├── config.json             # Configuration
├── requirements.txt        # Python dependencies
├── ocr_queue.example.json  # Example queue file
├── debug_*.scpt            # Debug scripts for UI element discovery
├── logs/                   # Runtime logs
├── output/                 # OCR output directory
└── reports/                # Generated reports
```

---

## How It Works / 技术原理

This tool uses **AppleScript UI Scripting** (System Events) to automate PDF Expert's GUI:

1. Open PDF in background (`open -g`) to avoid window maximization
2. Activate PDF Expert and bring to frontmost (`activate` + `set frontmost to true`)
3. Click menu: **Scan → Recognize Text** (扫描 → 识别文本)
4. Wait for right panel to appear, then click the **"识别..."** button
5. Handle confirmation dialog ("All pages" → "Apply")
6. Poll for OCR completion (check progress indicator disappearance)
7. Exit scan mode, save (⌘S), close window (⌘W)
8. Quit PDF Expert (single-file mode) or keep open (batch mode)

**Why foreground is required:** PDF Expert's right-side OCR panel only renders when the app is truly frontmost. Background accessibility clicks can trigger menus but the panel elements are not exposed in the accessibility tree.

---

## Troubleshooting / 常见问题

### Q: AppleScript returns "failed: window not found" / "窗口未找到"
A: PDF Expert may need more time to load. Increase the delay after `open -g` in `pdfexpert_ocr.scpt` (line 17, default 4s).

### Q: "failed: button not found" / "未找到识别...按钮"
A: The right panel did not load. Usually caused by:
- PDF Expert not being frontmost (another app stole focus)
- System popup blocking the UI (grant accessibility permissions)
- PDF Expert UI language mismatch (script expects Chinese UI)

### Q: OCR completes but validation shows 0 characters
A: Some PDF encodings (PNG + DeviceGray) are not supported by PDF Expert's OCR engine. No workaround — use alternative OCR tools for these files.

### Q: Processing is slow
A: Each file requires foreground activation. Do not use the mouse/keyboard during batch processing. For large PDFs (>100 pages), use the chunking approach described above.

### Q: How to switch OCR language?
A: Change `ocr_language` in `config.json`. The script currently supports the language options available in PDF Expert's UI. For English PDFs, use `"English"`.

---

## Known Limitations / 已知限制

1. **macOS only**: Requires AppleScript and System Events accessibility
2. **PDF Expert dependency**: UI element names are hardcoded in Chinese. If PDF Expert updates its UI, the script may need adjustment.
3. **Foreground requirement**: Cannot run in true headless mode — PDF Expert must be frontmost during OCR
4. **Serial processing**: Only one file at a time due to Accessibility API constraints
5. **PNG Gray-scale**: Some PDF image encodings are not OCR-able by PDF Expert
6. **Chinese UI only**: Currently tested with PDF Expert's Chinese interface. Other languages may require element name adjustments in `pdfexpert_ocr.scpt`

---

## Debug Scripts / 调试脚本

If PDF Expert updates its UI and element names change, use the debug scripts to discover new element names:

```bash
# List all UI elements containing "识别"
osascript debug_search.scpt /path/to/test.pdf

# Deep recursive search of the window element tree
osascript debug_panel.scpt /path/to/test.pdf

# Full dump of all accessible elements
osascript full_dump.scpt /path/to/test.pdf

# Find language radio buttons
osascript debug_lang.scpt /path/to/test.pdf
```

---

## License / 许可证

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments / 致谢

- [PDF Expert](https://pdfexpert.com/) by Readdle — excellent PDF OCR engine
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) — fast PDF text extraction and manipulation
