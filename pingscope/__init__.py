import os
from pathlib import Path

__version__ = "0.014"  # also add a section to CHANGELOG.md for every new version


def _commit():
    """Short git commit of the running code: PINGSCOPE_COMMIT (set when the image is built) or the checkout's .git."""
    c = os.environ.get("PINGSCOPE_COMMIT", "").strip()
    if c:
        return c[:7]
    git = Path(__file__).resolve().parent.parent / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        if (git / ref).exists():
            return (git / ref).read_text().strip()[:7]
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        pass
    return ""


__commit__ = _commit()
