
import re

MAX_INPUT_BYTES = 2 * 1024 * 1024


def execute_low_level_parse(raw_payload: bytes) -> str:
    """Decode and normalize a bounded UTF-8 text payload."""
    if not isinstance(raw_payload, bytes):
        raise TypeError("Payload must be bytes.")

    if not raw_payload:
        raise ValueError("Payload is empty.")

    if len(raw_payload) > MAX_INPUT_BYTES:
        raise ValueError("Payload exceeds the 2 MiB limit.")

    try:
        decoded = raw_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Input must be valid UTF-8 text.") from exc

    # Preserve tabs and newlines until whitespace normalization.
    decoded = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", decoded)
    decoded = re.sub(r"[ \t\r\n]+", " ", decoded).strip()

    if not decoded:
        raise ValueError("No usable text was found in the input.")

    return decoded
