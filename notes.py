"""
notes.py – Onglet Notes : un fichier JSON par note (notes/note_<slot>.json),
ordre des onglets dans notes/meta.json.
"""

import threading

from storage import note_store

TITLE_LEN = 24


class Note:
    def __init__(self, slot: int, label: str = "", content: str = ""):
        self.slot = slot
        self.label = label
        self.content = content

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        first = self.content.strip().split("\n")[0].strip() if self.content.strip() else ""
        return first[:TITLE_LEN] + ("…" if len(first) > TITLE_LEN else "")

    def to_ui(self) -> dict:
        return {"slot": self.slot, "label": self.label, "title": self.title,
                "content": self.content}

    def to_json(self) -> dict:
        return {"slot": self.slot, "label": self.label, "content": self.content}


class NotesService:
    def __init__(self):
        self._lock = threading.RLock()
        self._notes: list[Note] = []
        for slot in note_store.order():
            data = note_store.load(slot) or {}
            self._notes.append(Note(slot, data.get("label", ""), data.get("content", "")))
        if not self._notes:
            n = Note(self._next_slot())
            self._notes.append(n)
            note_store.save(n.slot, n.to_json())
            self._save_order()

    def _next_slot(self) -> int:
        used = set(note_store.existing_slots()) | {n.slot for n in self._notes}
        return max(used, default=0) + 1

    def _find(self, slot: int) -> Note | None:
        with self._lock:
            return next((n for n in self._notes if n.slot == slot), None)

    def _save_order(self):
        with self._lock:
            slots = [n.slot for n in self._notes]
        note_store.save_order(slots)

    def snapshot(self) -> dict:
        with self._lock:
            return {"tabs": [n.to_ui() for n in self._notes]}

    def new(self, after_slot: int | None = None) -> dict:
        with self._lock:
            n = Note(self._next_slot())
            idx = next((i for i, x in enumerate(self._notes) if x.slot == after_slot), None)
            if idx is None:
                self._notes.append(n)
            else:
                self._notes.insert(idx + 1, n)
        note_store.save(n.slot, n.to_json())
        self._save_order()
        return n.to_ui()

    def close(self, slot: int) -> dict:
        """Ferme une note. La dernière note est vidée plutôt que fermée."""
        n = self._find(slot)
        if not n:
            return {"removed": False}
        with self._lock:
            if len(self._notes) <= 1:
                n.label, n.content = "", ""
                note_store.save(n.slot, n.to_json())
                return {"removed": False, "reset": n.to_ui()}
            self._notes.remove(n)
        note_store.delete(slot)
        self._save_order()
        return {"removed": True}

    def rename(self, slot: int, label: str) -> dict | None:
        n = self._find(slot)
        if not n:
            return None
        n.label = label.strip()
        note_store.save(n.slot, n.to_json())
        return n.to_ui()

    def save(self, slot: int, content: str) -> dict | None:
        n = self._find(slot)
        if not n:
            return None
        n.content = content
        note_store.save(n.slot, n.to_json())
        return n.to_ui()
