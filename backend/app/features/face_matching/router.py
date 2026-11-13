"""Reading live selfie frames from a request (used by POST /discover/search).

The frames come as one raw body (Content-Type application/x-facescan-frames): each frame is a
4-byte big-endian length followed by that many bytes of JPEG/PNG/WebP. The body is read
straight into memory with size caps, so frames are never spooled to a temporary file
(invariant 3).
"""

from starlette.requests import Request

from app.core.errors import PayloadTooLargeError, UnprocessableError
from app.core.rate_limit import RateLimiter

search_limiter = RateLimiter("search", limit=30, window_seconds=60)
FRAMES_CONTENT_TYPE = "application/x-facescan-frames"
LENGTH_BYTES = 4


def split_frames(body: bytes, max_frames: int, max_frame_bytes: int) -> list[bytes]:
    """
    Purpose: parse the length-prefixed frames. Raises UnprocessableError or
        PayloadTooLargeError. Side effects: none.
    """
    frames: list[bytes] = []
    offset = 0
    while offset < len(body):
        if len(frames) >= max_frames:
            raise UnprocessableError(f"at most {max_frames} frames")
        if offset + LENGTH_BYTES > len(body):
            raise UnprocessableError("malformed frames body")
        size = int.from_bytes(body[offset : offset + LENGTH_BYTES], "big")
        offset += LENGTH_BYTES
        if size == 0 or size > max_frame_bytes:
            raise PayloadTooLargeError("a frame is empty or larger than allowed")
        if offset + size > len(body):
            raise UnprocessableError("malformed frames body")
        frames.append(body[offset : offset + size])
        offset += size
    return frames


async def read_frames(request: Request, max_frames: int, max_frame_bytes: int) -> list[bytes]:
    """Purpose: read the raw body into memory (size-capped) and split it into frames."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != FRAMES_CONTENT_TYPE:
        raise UnprocessableError(f"send the frames as {FRAMES_CONTENT_TYPE}")
    limit = max_frames * (max_frame_bytes + LENGTH_BYTES)
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise PayloadTooLargeError("selfie frames are larger than allowed")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError("selfie frames are larger than allowed")
        chunks.append(chunk)
    return split_frames(b"".join(chunks), max_frames, max_frame_bytes)
