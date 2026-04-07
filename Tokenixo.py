#!/usr/bin/env python3
"""
Tokenixo - Offline Token Counter v26.0.0
========================================
A standalone window app to count tokens. Real-time counting as you type.

Author: Duzf7
License: MIT
Repository: https://github.com/Duzf7/Tokenixo

Usage:
    python Tokenixo.py

Dependencies (at least one required):
    pip install tiktoken
    OR
    pip install tokenizers
"""

import sys
import platform
import subprocess
import tkinter as tk
from tkinter import scrolledtext, filedialog
import threading
from bisect import bisect_left, bisect_right


# ---------------------------------------------------------------------------
# Tokenizer loading (runs in background thread)
# ---------------------------------------------------------------------------

def _measure_dict_size(d):
    """Deep size of a dict with simple key/value types."""
    size = sys.getsizeof(d)
    for k, v in d.items():
        size += sys.getsizeof(k) + sys.getsizeof(v)
    return size


def _load_tiktoken():
    import tiktoken  # type: ignore[reportMissingImports]
    enc = tiktoken.get_encoding("cl100k_base")

    # Measure real memory footprint of encoding data
    size = _measure_dict_size(enc._mergeable_ranks)
    if hasattr(enc, '_special_tokens'):
        size += _measure_dict_size(enc._special_tokens)

    def encode(text):
        return enc.encode(text)

    def token_spans(text):
        tokens = enc.encode(text)
        spans = []
        offset = 0
        for tok_id in tokens:
            piece = enc.decode([tok_id])
            end = offset + len(piece)
            spans.append((offset, end))
            offset = end
        return spans

    return encode, token_spans, size


def _load_xenova():
    from transformers import GPT2TokenizerFast  # type: ignore[reportMissingImports]
    try:
        tok = GPT2TokenizerFast.from_pretrained(
            'Xenova/claude-tokenizer', local_files_only=True
        )
    except Exception:
        tok = GPT2TokenizerFast.from_pretrained('Xenova/claude-tokenizer')

    # Measure real memory footprint — use serialized tokenizer data size
    try:
        size = len(tok.backend_tokenizer.to_str().encode('utf-8'))
    except Exception:
        size = _measure_dict_size(tok.get_vocab())

    def encode(text):
        return tok.encode(text)

    def token_spans(text):
        encoding = tok(text, return_offsets_mapping=True)
        offsets = encoding.get("offset_mapping", [])
        if offsets:
            return [(s, e) for s, e in offsets if e > s]
        spans = []
        offset = 0
        for tok_id in encoding["input_ids"]:
            piece = tok.decode([tok_id])
            end = offset + len(piece)
            spans.append((offset, end))
            offset = end
        return spans

    return encode, token_spans, size


TOKENIZER_LOADERS = [
    ("tiktoken (cl100k_base)", _load_tiktoken),
    ("Xenova/claude-tokenizer", _load_xenova),
]


# ---------------------------------------------------------------------------
# Fast char-offset ↔ line.col conversion
# ---------------------------------------------------------------------------

def _build_line_index(text):
    """Build list of char offsets where each line starts. C-level str.find."""
    starts = [0]
    pos = 0
    while True:
        pos = text.find('\n', pos)
        if pos == -1:
            break
        pos += 1
        starts.append(pos)
    return starts


def _offset_to_linecol(offset, line_starts):
    """Convert a char offset to tkinter 'line.col' string. O(log n)."""
    line = bisect_right(line_starts, offset) - 1
    return f"{line + 1}.{offset - line_starts[line]}"


def _batch_offsets_to_linecol(offsets, line_starts):
    """Convert a monotonically non-decreasing list of offsets to linecol
    strings in a single O(n+m) linear sweep — no binary search per offset."""
    results = []
    line_idx = 0
    num_lines = len(line_starts)
    for off in offsets:
        while line_idx + 1 < num_lines and line_starts[line_idx + 1] <= off:
            line_idx += 1
        results.append(f"{line_idx + 1}.{off - line_starts[line_idx]}")
    return results


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_TAG_NAMES = [f"tok{i}" for i in range(6)]
_TAG_NAMES_BOLD = [f"tok{i}b" for i in range(6)]
_NUM_COLORS = len(_TAG_NAMES)

# Light mode highlight colors
_LIGHT_COLORS_SOFT = ["#EBF0FE", "#FEF8E3", "#E6FCF1", "#FDF1F8", "#EEEFFF", "#FEF1C4"]
_LIGHT_COLORS_BOLD = ["#B8D4FD", "#FDE69A", "#A8F0CE", "#F9C8E1", "#C4CCFF", "#FCD650"]
_LIGHT_BG, _LIGHT_FG = "#ffffff", "#000000"

# Dark mode highlight colors
_DARK_COLORS_SOFT = ["#1a3154", "#2e2800", "#0a2a18", "#2e0e20", "#1c1a3c", "#2e2300"]
_DARK_COLORS_BOLD = ["#1e5ca8", "#7a6200", "#0d7a3c", "#7a1e5c", "#2e2e9e", "#7a6200"]
_DARK_BG, _DARK_FG = "#1e1e1e", "#d4d4d4"


def _is_dark_mode():
    """Return True if the OS is currently in dark mode."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True
            )
            return result.returncode == 0 and result.stdout.strip() == "Dark"
    except Exception:
        pass
    return False


class TokenCounterApp:
    def __init__(self):
        self.tokenizers = {}
        self.method = None
        self.encode_func = None
        self.spans_func = None
        self._loading = True
        self._has_counted = False
        self._full_results = None
        self._cached_spans = None
        self._cached_span_starts = None
        self._cached_line_starts = None
        self._last_text = None
        self._count_gen = 0
        self._visible_tag_range = None
        self._recount_after_id = None
        self._scroll_after_id = None
        self._dark_mode = _is_dark_mode()

        self.root = tk.Tk()
        self.root.title("Tokenixo v26.1.4 - Offline Token Counter")
        w, h = 1000, 750
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(700, 500)

        self._build_ui()
        self._apply_theme()
        self._poll_dark_mode()

        threading.Thread(target=self._load_tokenizers, daemon=True).start()

    def _load_tokenizers(self):
        for name, loader in TOKENIZER_LOADERS:
            try:
                encode_func, spans_func, size_bytes = loader()
                self.tokenizers[name] = (encode_func, spans_func, size_bytes)
            except Exception:
                pass
        self.root.after(0, self._on_tokenizers_ready)

    def _on_tokenizers_ready(self):
        self._loading = False

        if not self.tokenizers:
            self.status_label.config(
                text="ERROR: No tokenizer found. pip install tiktoken or transformers"
            )
            return

        self.method = list(self.tokenizers.keys())[0]
        self.encode_func, self.spans_func, _ = self.tokenizers[self.method]

        menu = self.method_menu["menu"]
        menu.delete(0, "end")
        for name in self.tokenizers:
            _, _, size_bytes = self.tokenizers[name]
            size_mb = size_bytes / (1024 * 1024)
            label = f"{name} — {size_mb:.2f} MB"
            menu.add_command(label=label,
                            command=lambda n=name: self._on_method_change(n))
        self._update_method_label()

        self.status_label.config(text=f"Ready — {len(self.tokenizers)} tokenizer(s)")

        text = self.text_input.get("1.0", "end-1c")
        if text.strip():
            self._count()

    def _build_ui(self):
        root = self.root

        # Top bar
        top = tk.Frame(root)
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top, text="Tokenizer:", anchor="w").pack(side=tk.LEFT)
        self.method_var = tk.StringVar(value="Loading...")
        self.method_menu = tk.OptionMenu(top, self.method_var, "Loading...")
        self.method_menu.pack(side=tk.LEFT, padx=(5, 0))

        tk.Button(top, text="Open File...", command=self._open_file).pack(side=tk.RIGHT, padx=(5, 0))
        tk.Button(top, text="Clear", command=self._clear).pack(side=tk.RIGHT, padx=(5, 0))

        # Status label
        self.status_label = tk.Label(root, text="Loading tokenizers...", anchor="w", fg="gray")
        self.status_label.pack(fill=tk.X, padx=10)

        # Text input area
        tk.Label(root, text="Paste or type your text:", anchor="w").pack(fill=tk.X, padx=10)
        self.text_input = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Menlo", 13))
        self.text_input.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        # Pre-configure highlight tags — colors applied later by _apply_theme
        for tag in _TAG_NAMES:
            self.text_input.tag_configure(tag)
        for tag in _TAG_NAMES_BOLD:
            self.text_input.tag_configure(tag)
            self.text_input.tag_raise(tag)

        # Intercept scroll to update viewport highlights
        def _on_yscroll(*args):
            self.text_input.vbar.set(*args)
            self._schedule_viewport_update()
        self.text_input.configure(yscrollcommand=_on_yscroll)
        self.text_input.bind("<Configure>", lambda e: self._schedule_viewport_update())

        # Results area
        self.results_frame = tk.LabelFrame(root, text="Results", padx=10, pady=5)
        self.results_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.result_labels = {}
        fields = [
            ("Tokens", "tokens"),
            ("Characters", "characters"),
            ("Words", "words"),
            ("Lines", "lines"),
            ("Chars/Token", "cpt"),
        ]

        row = tk.Frame(self.results_frame)
        row.pack(fill=tk.X)
        for display_name, key in fields:
            cell = tk.Frame(row)
            cell.pack(side=tk.LEFT, expand=True, fill=tk.X)
            tk.Label(cell, text=display_name, font=("Helvetica", 11)).pack()
            lbl = tk.Label(cell, text="-", font=("Helvetica", 16, "bold"))
            lbl.pack()
            self.result_labels[key] = lbl

        # Context window usage
        tk.Label(self.results_frame, text="Context Window Usage:", anchor="w",
                 font=("Helvetica", 11)).pack(fill=tk.X, pady=(8, 2))
        self.context_labels = {}
        for name in ["Haiku 4.5", "Sonnet 4.6", "Opus 4.6"]:
            lbl = tk.Label(self.results_frame, text="", anchor="w")
            lbl.pack(fill=tk.X)
            self.context_labels[name] = lbl

        # Credit bar
        credit = tk.Label(root,
                          text="Tokenixo v26.1.4  |  By Duzf7  |  MIT License",
                          anchor="center", fg="gray", font=("Helvetica", 10))
        credit.pack(fill=tk.X, padx=10, pady=(0, 5))

        # Real-time counting on text changes (typing, pasting, deleting)
        self.text_input.bind("<KeyRelease>", lambda e: self._schedule_recount())
        self.text_input.bind("<<Paste>>", lambda e: self.root.after(1, self._schedule_recount))
        self.text_input.bind("<<Cut>>", lambda e: self.root.after(1, self._schedule_recount))

        # Live selection tracking
        self.text_input.bind("<<Selection>>", lambda e: self._on_selection_change())
        self.text_input.bind("<ButtonRelease-1>", lambda e: self.root.after(10, self._on_selection_change))

    # ------------------------------------------------------------------
    # Dark mode
    # ------------------------------------------------------------------

    def _apply_theme(self):
        """Apply light or dark colors to the text widget and highlight tags."""
        dark = self._dark_mode
        soft = _DARK_COLORS_SOFT if dark else _LIGHT_COLORS_SOFT
        bold = _DARK_COLORS_BOLD if dark else _LIGHT_COLORS_BOLD
        bg   = _DARK_BG          if dark else _LIGHT_BG
        fg   = _DARK_FG          if dark else _LIGHT_FG

        self.text_input.configure(
            background=bg,
            foreground=fg,
            insertbackground=fg,
            selectbackground=bg,
            selectforeground=fg,
        )

        for tag, color in zip(_TAG_NAMES, soft):
            self.text_input.tag_configure(tag, background=color)
        for tag, color in zip(_TAG_NAMES_BOLD, bold):
            self.text_input.tag_configure(tag, background=color)

        # Force a re-render of visible highlights with new colors
        self._visible_tag_range = None
        self._apply_visible_highlights()

    def _poll_dark_mode(self):
        """Check every 2 s if the OS theme changed and re-apply if needed."""
        current = _is_dark_mode()
        if current != self._dark_mode:
            self._dark_mode = current
            self._apply_theme()
        self.root.after(2000, self._poll_dark_mode)

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def _schedule_recount(self):
        if self._recount_after_id is not None:
            self.root.after_cancel(self._recount_after_id)
        self._recount_after_id = self.root.after(150, self._recount_if_changed)

    def _recount_if_changed(self):
        text = self.text_input.get("1.0", "end-1c")
        if text != self._last_text:
            self._count()

    def _schedule_viewport_update(self):
        if self._scroll_after_id is not None:
            self.root.after_cancel(self._scroll_after_id)
        self._scroll_after_id = self.root.after(16, self._apply_visible_highlights)

    # ------------------------------------------------------------------
    # Tokenizer switch
    # ------------------------------------------------------------------

    def _update_method_label(self):
        """Update the dropdown display to show current tokenizer with size."""
        _, _, size_bytes = self.tokenizers[self.method]
        size_mb = size_bytes / (1024 * 1024)
        self.method_var.set(f"{self.method} — {size_mb:.2f} MB")

    def _on_method_change(self, selection):
        self.method = selection
        self.encode_func, self.spans_func, _ = self.tokenizers[selection]
        self._update_method_label()
        self._has_counted = False
        self._full_results = None
        self._count()

    # ------------------------------------------------------------------
    # Viewport-only highlighting
    # ------------------------------------------------------------------

    def _get_visible_char_range(self):
        """Return (start_offset, end_offset) of the visible text region."""
        ti = self.text_input
        ls = self._cached_line_starts
        if not ls:
            return 0, 0
        first_idx = ti.index("@0,0")
        last_idx = ti.index(f"@{ti.winfo_width()},{ti.winfo_height()}")
        fl, fc = map(int, first_idx.split('.'))
        ll, lc = map(int, last_idx.split('.'))
        first_off = ls[min(fl - 1, len(ls) - 1)] + fc
        last_off = ls[min(ll - 1, len(ls) - 1)] + lc
        return first_off, last_off

    def _apply_visible_highlights(self):
        """Apply soft highlight tags only for tokens visible in the viewport
        plus a margin. Skips if the visible range hasn't changed."""
        if not self._cached_spans or not self._cached_line_starts:
            return

        ti = self.text_input
        ls = self._cached_line_starts
        spans = self._cached_spans
        span_starts = self._cached_span_starts

        vis_start, vis_end = self._get_visible_char_range()

        # Expand by a margin for smooth scrolling
        margin = 200
        first_idx = max(0, bisect_left(span_starts, vis_start) - margin)
        last_idx = min(len(spans), bisect_right(span_starts, vis_end) + margin)

        new_range = (first_idx, last_idx)
        if new_range == self._visible_tag_range:
            return
        self._visible_tag_range = new_range

        # Remove old soft tags
        for tag in _TAG_NAMES:
            ti.tag_remove(tag, "1.0", tk.END)

        visible_spans = spans[first_idx:last_idx]
        if not visible_spans:
            return

        # Batch convert all offsets in one linear sweep
        flat_offsets = []
        for s, e in visible_spans:
            flat_offsets.append(s)
            flat_offsets.append(e)
        linecols = _batch_offsets_to_linecol(flat_offsets, ls)

        # Group by color and apply — one Tcl call per color
        batches = [[] for _ in range(_NUM_COLORS)]
        for j in range(len(visible_spans)):
            batches[(first_idx + j) % _NUM_COLORS].append(
                (linecols[2 * j], linecols[2 * j + 1])
            )

        for color_idx, batch in enumerate(batches):
            if not batch:
                continue
            tag = _TAG_NAMES[color_idx]
            args = []
            for s, e in batch:
                args.append(s)
                args.append(e)
            ti.tk.call(ti._w, "tag", "add", tag, *args)

    # ------------------------------------------------------------------
    # Bold highlight for selection
    # ------------------------------------------------------------------

    def _remove_bold_highlights(self):
        ti = self.text_input
        for tag in _TAG_NAMES_BOLD:
            ti.tag_remove(tag, "1.0", tk.END)

    def _apply_bold_to_range(self, sel_start_off, sel_end_off):
        """Apply bold color tags only to token spans overlapping the selection.
        Uses bisect to skip non-overlapping spans — O(selection_tokens)."""
        if not self._cached_spans or not self._cached_line_starts:
            return
        ti = self.text_input
        ls = self._cached_line_starts
        spans = self._cached_spans
        span_starts = self._cached_span_starts

        first = max(0, bisect_left(span_starts, sel_start_off) - 1)
        last = bisect_right(span_starts, sel_end_off)

        flat_offsets = []
        indices = []
        for i in range(first, min(last, len(spans))):
            start, end = spans[i]
            if end <= sel_start_off or start >= sel_end_off:
                continue
            clamped_s = max(start, sel_start_off)
            clamped_e = min(end, sel_end_off)
            flat_offsets.append(clamped_s)
            flat_offsets.append(clamped_e)
            indices.append(i)

        if not flat_offsets:
            return

        linecols = _batch_offsets_to_linecol(flat_offsets, ls)

        batches = [[] for _ in range(_NUM_COLORS)]
        for j, idx in enumerate(indices):
            batches[idx % _NUM_COLORS].append((linecols[2 * j], linecols[2 * j + 1]))

        for color_idx, batch in enumerate(batches):
            if not batch:
                continue
            tag = _TAG_NAMES_BOLD[color_idx]
            args = []
            for s, e in batch:
                args.append(s)
                args.append(e)
            ti.tk.call(ti._w, "tag", "add", tag, *args)

    # ------------------------------------------------------------------
    # Selection change
    # ------------------------------------------------------------------

    def _on_selection_change(self):
        """When user selects text, show stats for selection only."""
        if not self._has_counted or self.encode_func is None:
            return

        self._remove_bold_highlights()

        try:
            sel_text = self.text_input.get(tk.SEL_FIRST, tk.SEL_LAST)
            content = self.text_input.get("1.0", "end-1c")
            if len(sel_text) > len(content):
                sel_text = content
        except tk.TclError:
            if self._full_results:
                self._update_display(*self._full_results, label="Results")
            return

        if not sel_text.strip():
            if self._full_results:
                self._update_display(*self._full_results, label="Results")
            return

        if self._cached_line_starts:
            sel_first = self.text_input.index(tk.SEL_FIRST)
            sel_last = self.text_input.index(tk.SEL_LAST)
            sl, sc = map(int, sel_first.split('.'))
            el, ec = map(int, sel_last.split('.'))
            sel_start_off = self._cached_line_starts[sl - 1] + sc
            sel_end_off = self._cached_line_starts[el - 1] + ec
            self._apply_bold_to_range(sel_start_off, sel_end_off)

        tokens = len(self.encode_func(sel_text))
        characters = len(sel_text)
        words = len(sel_text.split())
        lines = sel_text.count('\n') + (1 if sel_text else 0)
        cpt = round(characters / tokens, 2) if tokens > 0 else 0
        self._update_display(tokens, characters, words, lines, cpt,
                             label="Results (selection)")

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _update_display(self, tokens, characters, words, lines, cpt, label="Results"):
        self.results_frame.config(text=label)
        self.result_labels["tokens"].config(text=f"{tokens:,}")
        self.result_labels["characters"].config(text=f"{characters:,}")
        self.result_labels["words"].config(text=f"{words:,}")
        self.result_labels["lines"].config(text=f"{lines:,}")
        self.result_labels["cpt"].config(text=f"{cpt}")

        models = [
            ("Haiku 4.5",  200_000),
            ("Sonnet 4.6", 1_000_000),
            ("Opus 4.6",   1_000_000),
        ]
        for name, limit in models:
            pct = tokens / limit * 100
            self.context_labels[name].config(
                text=f"  {name:12s}  {tokens:>10,} / {limit:>10,}  ({pct:.4f}%)"
            )

    # ------------------------------------------------------------------
    # Highlight management
    # ------------------------------------------------------------------

    def _remove_highlights(self):
        ti = self.text_input
        for tag in _TAG_NAMES + _TAG_NAMES_BOLD:
            ti.tag_remove(tag, "1.0", tk.END)
        self._visible_tag_range = None

    # ------------------------------------------------------------------
    # Count (background threaded)
    # ------------------------------------------------------------------

    def _count(self):
        if self._loading or self.encode_func is None:
            return

        text = self.text_input.get("1.0", "end-1c")

        if not text.strip():
            self._remove_highlights()
            self._has_counted = False
            self._full_results = None
            self._cached_spans = None
            self._cached_span_starts = None
            self._cached_line_starts = None
            self._last_text = text
            self.results_frame.config(text="Results")
            for lbl in self.result_labels.values():
                lbl.config(text="-")
            for lbl in self.context_labels.values():
                lbl.config(text="")
            return

        self._count_gen += 1
        gen = self._count_gen
        spans_func = self.spans_func

        def worker():
            try:
                spans = spans_func(text)
            except Exception:
                spans = None

            if not spans:
                self.root.after(0, lambda: self._on_count_done(
                    gen, text, None, None, None))
                return

            line_starts = _build_line_index(text)
            span_starts = [s for s, _ in spans]

            self.root.after(0, lambda: self._on_count_done(
                gen, text, spans, span_starts, line_starts))

        threading.Thread(target=worker, daemon=True).start()

    def _on_count_done(self, gen, text, spans, span_starts, line_starts):
        """Callback on main thread after background tokenization completes."""
        if gen != self._count_gen:
            return

        self._cached_spans = spans
        self._cached_span_starts = span_starts
        self._cached_line_starts = line_starts
        self._last_text = text
        self._visible_tag_range = None

        if not spans:
            self._remove_highlights()
            self._has_counted = False
            self._full_results = None
            return

        tokens = len(spans)
        characters = len(text)
        words = len(text.split())
        lines = text.count('\n') + (1 if text else 0)
        cpt = round(characters / tokens, 2) if tokens > 0 else 0

        self._full_results = (tokens, characters, words, lines, cpt)
        self._has_counted = True

        self._remove_highlights()
        self._apply_visible_highlights()

        self._update_display(tokens, characters, words, lines, cpt, label="Results")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _clear(self):
        self._count_gen += 1
        self._remove_highlights()
        self._has_counted = False
        self._full_results = None
        self._cached_spans = None
        self._cached_span_starts = None
        self._cached_line_starts = None
        self._last_text = None
        self.results_frame.config(text="Results")
        self.text_input.delete("1.0", tk.END)
        for lbl in self.result_labels.values():
            lbl.config(text="-")
        for lbl in self.context_labels.values():
            lbl.config(text="")

    def _open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Text files", "*.txt *.md *.py *.js *.ts *.json *.csv"), ("All files", "*.*")]
        )
        if path:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", content)
                self._count()
            except Exception as e:
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", f"Error reading file: {e}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    TokenCounterApp().run()
