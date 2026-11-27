import re
from dataclasses import dataclass
from os.path import commonprefix

BOUNDARY = re.compile(r"[^。！？!?\n]*(?:[。！？!?]+[」』”’)]*|\n)")
HAS_WORD = re.compile(r"[^\W_]", re.UNICODE)
MAX_PHRASE = 100


@dataclass(frozen=True, eq=False)
class Caption:
    text: str


def normalize(raw: str) -> str:
    return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


def scroll_offset(previous: str, text: str, prefix: int) -> int:
    if prefix == len(previous):
        return 0
    active_start = previous.rfind("\n") + 1
    for newline in re.finditer("\n", previous):
        start = newline.end()
        retained = previous[start:]
        matched = len(commonprefix((retained, text)))
        untouched = active_start - start
        if matched > prefix and (matched == len(retained) or 0 < untouched <= matched):
            return start
    if prefix < min(len(previous), 4):
        for size in range(min(len(previous), len(text)), 3, -1):
            if previous.endswith(text[:size]):
                return len(previous) - size
    return 0


class CaptionBuffer:
    """Track new text and revisions in the rolling Windows caption window."""

    def __init__(self):
        self.snapshot = ""
        self.offset = 0
        self.floor = 0
        self.pending = ""
        self.changed = 0.0
        self.last_preview = ""
        self.preview_after = 0.0
        self.spans: list[tuple[int, int, Caption]] = []
        self.invalidated: list[Caption] = []

    def prime(self, raw: str):
        self.snapshot = normalize(raw)
        self.floor = self.offset = len(self.snapshot)

    def update(self, raw: str, now: float) -> list[Caption]:
        text = normalize(raw)
        previous = self.snapshot
        self.invalidated = []
        if not text or text == previous:
            return []

        finished = []
        offset = self.offset
        prefix = len(commonprefix((previous, text)))
        shift = scroll_offset(previous, text, prefix)
        if shift:
            # The rolling window can drop an old line while correcting the active one.
            previous = previous[shift:]
            prefix = len(commonprefix((previous, text)))
            offset = max(0, offset - shift)
            self.floor = max(0, self.floor - shift)
            self.spans = [
                (max(0, start - shift), end - shift, piece)
                for start, end, piece in self.spans
                if end > shift
            ]
        if previous and not prefix:
            if HAS_WORD.search(self.pending):
                finished.append(Caption(self.pending))
            offset = 0
            self.floor = 0
            self.spans.clear()
        elif prefix < offset:
            self.floor = min(self.floor, len(text))
            revised = [
                (start, piece) for start, end, piece in self.spans if end > prefix
            ]
            if revised:
                offset = min(len(text), max(self.floor, revised[0][0]))
                self.invalidated = [piece for _, piece in revised]
                self.spans = [
                    (start, end, piece)
                    for start, end, piece in self.spans
                    if end <= prefix
                ]
            else:
                offset = min(offset, len(text))

        tail = text[offset:]
        start = offset
        for match in BOUNDARY.finditer(tail):
            offset = start + match.end()
            if HAS_WORD.search(match[0]):
                finished.append(self.commit(match[0], start + match.start(), offset))
        tail = text[offset:]

        while len(tail) > MAX_PHRASE:
            comma = max(
                tail.rfind("、", 30, MAX_PHRASE), tail.rfind("，", 30, MAX_PHRASE)
            )
            cut = comma + 1 if comma >= 0 else MAX_PHRASE
            finished.append(self.commit(tail[:cut], offset, offset + cut))
            offset += cut
            tail = tail[cut:]

        self.snapshot = text
        self.offset = offset
        tail = tail.strip()
        if not self.pending or finished:
            self.last_preview = ""
            self.preview_after = now + 1.5
        if tail != self.pending:
            self.changed = now
        self.pending = tail
        return finished

    def commit(self, text: str, start: int, end: int) -> Caption:
        piece = Caption(text.strip())
        self.spans.append((start, end, piece))
        return piece

    def finish_on_pause(self, now: float) -> Caption | None:
        if now - self.changed < 1.35:
            return None
        return self.finish_pending()

    def finish_pending(self) -> Caption | None:
        if not HAS_WORD.search(self.pending):
            return None
        piece = self.commit(self.pending, self.offset, len(self.snapshot))
        self.offset = len(self.snapshot)
        self.pending = ""
        self.last_preview = ""
        return piece

    def preview(self, now: float) -> str:
        if self.pending == self.last_preview or not HAS_WORD.search(self.pending):
            return ""
        settled = now - self.changed >= 0.45
        continuous = len(self.pending) >= 8 and now >= self.preview_after
        if len(self.pending) < 2 or not (settled or continuous):
            return ""
        self.last_preview = self.pending
        self.preview_after = now + 1.5
        return self.pending
