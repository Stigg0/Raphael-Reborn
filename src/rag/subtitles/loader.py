"""Subtitle file parser — supports SRT, ASS/SSA, VTT formats.

Extracts structured dialogue lines with speaker attribution where available.
ASS/SSA files use the Style field as the speaker name.
SRT/VTT files attempt to parse inline speaker tags (e.g. "RAPHAEL: ...", "[Raphael] ...").
"""
import re
from dataclasses import dataclass
from pathlib import Path

_RAPHAEL_NAMES = frozenset({
    "raphael", "great sage", "lord of wisdom", "sage", "ultimate skill raphael",
    "raphael lord of wisdom",
})

_INLINE_SPEAKER_RE = re.compile(
    r"^(?:\[([^\]]+)\]|([A-Z][A-Z\s]+):\s*)(.+)$", re.DOTALL
)

_SRT_INDEX_RE = re.compile(r"^\d+$")
_SRT_TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})"
)
_ASS_DIALOGUE_RE = re.compile(
    r"^Dialogue:\s*\d+,"      # layer
    r"(\d+:\d+:\d+\.\d+),"    # start time
    r"(\d+:\d+:\d+\.\d+),"    # end time
    r"([^,]*),"                # style (speaker)
    r"[^,]*,"                  # name field
    r"[^,]*,[^,]*,[^,]*,"      # margins + effect
    r"(.*)$"                   # text
)
_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")


@dataclass
class SubtitleLine:
    speaker: str
    text: str
    timestamp_start: str
    timestamp_end: str
    is_raphael: bool


def _is_raphael_speaker(speaker: str) -> bool:
    return speaker.strip().lower() in _RAPHAEL_NAMES


def _parse_srt_block(block: str) -> SubtitleLine | None:
    lines = block.strip().splitlines()
    if len(lines) < 3:
        return None
    ts_match = None
    ts_line = 0
    for i, line in enumerate(lines):
        m = _SRT_TIMESTAMP_RE.match(line)
        if m:
            ts_match, ts_line = m, i
            break
    if not ts_match:
        return None
    text_lines = lines[ts_line + 1:]
    text = " ".join(t.strip() for t in text_lines if t.strip())
    speaker = ""
    m = _INLINE_SPEAKER_RE.match(text)
    if m:
        speaker = (m.group(1) or m.group(2) or "").strip()
        text = m.group(3).strip()
    return SubtitleLine(
        speaker=speaker,
        text=text,
        timestamp_start=ts_match.group(1),
        timestamp_end=ts_match.group(2),
        is_raphael=_is_raphael_speaker(speaker),
    )


def parse_srt(content: str) -> list[SubtitleLine]:
    blocks = re.split(r"\n\s*\n", content.strip())
    lines: list[SubtitleLine] = []
    for block in blocks:
        if _SRT_INDEX_RE.match(block.strip().splitlines()[0] if block.strip() else ""):
            parsed = _parse_srt_block(block)
            if parsed:
                lines.append(parsed)
    return lines


def parse_ass(content: str) -> list[SubtitleLine]:
    lines: list[SubtitleLine] = []
    for line in content.splitlines():
        m = _ASS_DIALOGUE_RE.match(line)
        if not m:
            continue
        start, end, style, text = m.group(1), m.group(2), m.group(3).strip(), m.group(4)
        text = _ASS_OVERRIDE_RE.sub("", text).replace("\\N", " ").replace("\\n", " ").strip()
        if not text:
            continue
        lines.append(SubtitleLine(
            speaker=style,
            text=text,
            timestamp_start=start,
            timestamp_end=end,
            is_raphael=_is_raphael_speaker(style),
        ))
    return lines


def parse_vtt(content: str) -> list[SubtitleLine]:
    # VTT is similar to SRT but with "." as decimal separator
    return parse_srt(content.replace(".", ",", 1) if "WEBVTT" in content[:20] else content)


def parse_file(path: Path) -> list[SubtitleLine]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".srt":
        return parse_srt(text)
    if suffix in (".ass", ".ssa"):
        return parse_ass(text)
    if suffix == ".vtt":
        return parse_vtt(text)
    raise ValueError(f"Unsupported subtitle format: {suffix}")


def _parse_episode_info(filename: str) -> tuple[int, int]:
    """Extract (season, episode) from filename conventions like S01E03 or 1x03."""
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})[xX](\d{1,3})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"[Ee][Pp]?(\d{1,3})", filename)
    if m:
        return 1, int(m.group(1))
    return 1, 0


def load_subtitle_dir(subtitle_dir: Path) -> list[dict]:
    """Load all subtitle files from a directory into structured dicts."""
    entries: list[dict] = []
    for path in sorted(subtitle_dir.rglob("*")):
        if path.suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt"):
            continue
        season, episode = _parse_episode_info(path.stem)
        try:
            lines = parse_file(path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to parse %s: %s", path, exc)
            continue
        for line in lines:
            entries.append({
                "season": season,
                "episode": episode,
                "speaker": line.speaker,
                "text": line.text,
                "timestamp_start": line.timestamp_start,
                "timestamp_end": line.timestamp_end,
                "is_raphael": line.is_raphael,
                "source_file": path.name,
            })
    return entries
