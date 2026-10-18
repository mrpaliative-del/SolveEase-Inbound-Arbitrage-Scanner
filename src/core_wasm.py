
import re

# Limit raw input to protect the service from oversized payloads.
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB

# Remove unsafe control characters while preserving tabs and line breaks
# long enough to normalize them into spaces.
_CONTROL_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]"
)

_WHITESPACE = re.compile(r"\s+")


def execute_low_level_parse(raw_payload: bytes) -> str:
    """
    Normalize UTF-8 text received as bytes.

    This is a Python preprocessing implementation, not a WebAssembly
    runtime or a general-purpose binary parser.
    """
    if not isinstance(raw_payload, bytes):
        raise TypeError("raw_payload must be bytes")

    if len(raw_payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload exceeds the 2 MiB size limit")

    try:
        decoded_string = raw_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Payload is not valid UTF-8 text"
        ) from exc

    # Remove unwanted control characters.
    cleaned_text = _CONTROL_CHARS.sub("", decoded_string)

    # Normalize tabs, newlines and repeated spaces.
    optimized_text = _WHITESPACE.sub(" ", cleaned_text).strip()

    return optimized_text
