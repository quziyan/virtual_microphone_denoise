#!/usr/bin/env python3
"""WER/CER benchmark across Hush suppression levels.

Goal: produce the paste-ready comparison table for "不同强度下识别准确率" —
record ONE clip of yourself speaking while background human voices play, give
the script the reference text of what you actually said, and it will:

  1. Render that clip at every suppression level (原始/Gentle/Medium/Strong/
     Aggressive) using the real Hush pipeline (src/denoise_file.py).
  2. Transcribe each render with Whisper.
  3. Compute the character error rate (CER, the right metric for Chinese) vs
     your reference text.
  4. Print a Markdown table you can drop straight into the article, and save
     the rendered WAVs + each transcription under --outdir.

Usage::

    # reference text inline
    python tests/wer_bench.py --input my_clip.wav --reference-text "我说的那段话原文"

    # or reference text from a file
    python tests/wer_bench.py --input my_clip.wav --reference ref.txt --model small

The clip must contain BOTH your voice and the competing background speech —
that's the whole point of the test. Any sample rate / mono or stereo is fine.

Transcription needs Whisper. Install ONE of (CPU is fine on Apple Silicon)::

    pip install faster-whisper      # preferred, faster
    pip install -U openai-whisper   # fallback

For Chinese, --model small or medium is a good accuracy/speed trade-off.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from denoise_file import denoise, load_wav, save_wav  # noqa: E402

# (key, 文章里的档位名, atten_db | None=原始直通). Mirrors src/tuning.py LEVELS.
LEVELS = [
    ("raw", "原始(不处理)", None),
    ("gentle", "Gentle(轻)", 20.0),
    ("medium", "Medium(中)", 40.0),
    ("strong", "Strong(强)", 60.0),
    ("aggressive", "Aggressive(最强)", 100.0),
]


# --- CER (character error rate) -------------------------------------------

def normalize(s: str) -> str:
    """Keep CJK / latin / digits; drop everything else (spaces, punctuation)."""
    s = s.lower()
    return re.sub(r"[^一-鿿぀-ヿa-z0-9]", "", s)


def edit_distance(ref: str, hyp: str) -> int:
    """Levenshtein distance over characters (pure Python, no deps)."""
    m, n = len(ref), len(hyp)
    if m == 0:
        return n
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def cer(ref: str, hyp: str) -> float:
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0
    return edit_distance(r, h) / len(r)


# --- Whisper transcription (load model once, reuse per level) --------------

def build_transcriber(model_name: str, language: str):
    """Return a transcribe(wav_path)->str callable, or exit with an install hint."""
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(model_name, device="cpu", compute_type="int8")

        def _t(wav_path: Path) -> str:
            segments, _ = model.transcribe(str(wav_path), language=language)
            return "".join(seg.text for seg in segments).strip()

        print(f"[whisper] faster-whisper '{model_name}' (cpu/int8)")
        return _t
    except ImportError:
        pass

    try:
        import whisper  # type: ignore

        model = whisper.load_model(model_name)

        def _t(wav_path: Path) -> str:
            return model.transcribe(str(wav_path), language=language)["text"].strip()

        print(f"[whisper] openai-whisper '{model_name}'")
        return _t
    except ImportError:
        sys.exit(
            "需要安装 Whisper 才能转写:\n"
            "    pip install faster-whisper      # 推荐\n"
            "    pip install -U openai-whisper   # 备选"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark CER across Hush levels.")
    p.add_argument("--input", required=True, help="带你说话+背景人声的录音 WAV")
    ref = p.add_mutually_exclusive_group(required=True)
    ref.add_argument("--reference", help="参考文稿 .txt 路径(你实际念的原文)")
    ref.add_argument("--reference-text", help="参考文稿原文(直接传字符串)")
    p.add_argument("--model", default="small", help="Whisper 模型 (base/small/medium)")
    p.add_argument("--language", default="zh", help="转写语言,默认 zh")
    p.add_argument("--outdir", default="/tmp/wer_bench", help="渲染音频与转写输出目录")
    args = p.parse_args()

    reference = (
        Path(args.reference).read_text(encoding="utf-8")
        if args.reference
        else args.reference_text
    )
    in_path = Path(args.input).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    audio, sr = load_wav(in_path)
    dur = len(audio) / sr
    print(f"Loaded {in_path.name}: {dur:.1f}s @ {sr} Hz")
    print(f"参考文稿({len(normalize(reference))} 字,归一化后)\n")

    transcribe = build_transcriber(args.model, args.language)

    rows = []
    for key, label, atten in LEVELS:
        rendered = audio if atten is None else denoise(audio, sr, atten)
        wav_path = outdir / f"{key}.wav"
        save_wav(wav_path, rendered, sr)

        hyp = transcribe(wav_path)
        score = cer(reference, hyp)
        (outdir / f"{key}.txt").write_text(hyp, encoding="utf-8")

        rows.append((label, score, hyp))
        print(f"  {label:<16} CER={score*100:5.1f}%  ->  {hyp[:40]}")

    # --- paste-ready Markdown table (matches the article columns) ----------
    table = ["", "| 降噪强度 | 转写字错率(CER) | 主观听感 |", "|---|---|---|"]
    for label, score, _hyp in rows:
        table.append(f"| {label} | {score*100:.1f}% | 【填你的听感】 |")
    md = "\n".join(table)
    (outdir / "table.md").write_text(md + "\n", encoding="utf-8")

    print("\n=== 可直接粘进文章的表格(听感栏请自己填)===")
    print(md)
    print(f"\n渲染音频/转写/表格已存到: {outdir}")
    print("提示:CER 越低越好;听感请戴耳机逐个试听 outdir 里的 .wav 后填写。")


if __name__ == "__main__":
    main()
