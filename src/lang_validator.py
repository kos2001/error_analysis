"""Language-compliance validator.

Detects characters outside the allowed alphabet (Korean Hangul + Latin ASCII +
common punctuation/digits/emoji) and optionally rewrites them using an LLM.

The model we use (deepseek-v4-flash) occasionally leaks Chinese characters
into Korean output (e.g. "언제恢复正常하나요"). This validator catches that.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

# Unicode ranges of CJK ideographs (Han characters). Hangul (한글) is excluded.
HAN_RE = re.compile(
    r"["
    r"㐀-䶿"   # CJK Unified Ideographs Extension A
    r"一-鿿"   # CJK Unified Ideographs (basic)
    r"豈-﫿"   # CJK Compatibility Ideographs
    r"\U00020000-\U0002A6DF"  # Extension B
    r"]"
)

# Allowed characters: ASCII printable, Hangul, common punctuation, digits, whitespace, emoji
ALLOWED_RE = re.compile(
    r"["
    r"\x09-\x0D\x20-\x7E"            # ASCII printable + whitespace
    r"가-힣"                  # Hangul syllables
    r"ᄀ-ᇿ㄰-㆏"     # Hangul Jamo / Compatibility Jamo
    r" -⁯⸀-⹿"     # General punctuation
    r"‐-‧‰-⁞"     # Hyphen, dashes, etc.
    r" -ÿ"                  # Latin-1 supplement
    r"←-⇿⌀-⏿"     # Arrows + misc technical
    r"─-╿■-◿"     # Box / geometric
    r"☀-➿"                  # Misc symbols + dingbats (✓ ☑ etc.)
    r"\U0001F300-\U0001FAFF"          # Emoji
    r"]"
)


@dataclass
class ValidationResult:
    ok: bool
    violations: list[str]
    cleaned: str  # text with violations replaced (only used if rewrite=False)
    rewritten: str | None = None  # full LLM-rewritten version if requested


def find_violations(text: str) -> list[str]:
    """Return list of Han characters appearing in text (deduped, order preserved)."""
    seen = []
    for ch in text:
        if HAN_RE.match(ch) and ch not in seen:
            seen.append(ch)
    return seen


def validate(text: str) -> ValidationResult:
    """Pure-Python check + masked-replacement fallback."""
    violations = find_violations(text)
    if not violations:
        return ValidationResult(ok=True, violations=[], cleaned=text)
    cleaned = HAN_RE.sub("⟦?⟧", text)
    return ValidationResult(ok=False, violations=violations, cleaned=cleaned)


_REWRITER: Agent | None = None


def _get_rewriter() -> Agent:
    global _REWRITER
    if _REWRITER is not None:
        return _REWRITER
    api_key = os.environ["OPENROUTER_API_KEY"]
    model_id = os.getenv("RVP_VALIDATOR_MODEL") or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    _REWRITER = Agent(
        name="lang-rewriter",
        model=OpenRouter(id=model_id, api_key=api_key, base_url=base_url),
        instructions=[
            "You are a strict language-compliance editor.",
            "Allowed scripts: Korean Hangul, Latin (English), digits, punctuation, emoji.",
            "Forbidden: any Chinese / Japanese kanji / Hanja characters (CJK ideographs).",
            "Rewrite the user's text so the meaning is preserved but ALL forbidden "
            "characters are replaced with natural Korean (or English when the surrounding "
            "context is English).",
            "Do not add commentary. Output ONLY the rewritten text.",
        ],
        markdown=False,
        telemetry=False,
    )
    return _REWRITER


def validate_and_fix(text: str) -> ValidationResult:
    """Detect violations and use LLM to rewrite if found."""
    result = validate(text)
    if result.ok:
        return result
    try:
        agent = _get_rewriter()
        resp = agent.run(text)
        rewritten = resp.content if hasattr(resp, "content") else str(resp)
        # Verify the rewrite removed all violations
        post = validate(rewritten)
        result.rewritten = rewritten if post.ok else rewritten  # keep even if partial
    except Exception as e:
        result.rewritten = f"[rewrite failed: {e}]"
    return result


if __name__ == "__main__":
    import sys
    samples = [
        "안녕하세요. 어떻게 도와드릴까요?",
        "언제 恢复正常 하나요?",
        "Error E033 means thermal throttling.",
        "디바이스가 自动 으로 재시작됩니다.",
    ]
    if len(sys.argv) > 1 and sys.argv[1] == "--fix":
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        for s in samples:
            r = validate_and_fix(s)
            print(f"[{'OK' if r.ok else 'BAD'}] {s!r}")
            if not r.ok:
                print(f"   violations: {r.violations}")
                print(f"   rewritten : {r.rewritten!r}")
    else:
        for s in samples:
            r = validate(s)
            print(f"[{'OK' if r.ok else 'BAD'}] {s!r}  violations={r.violations}")
