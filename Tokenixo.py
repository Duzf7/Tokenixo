#!/usr/bin/env python3
"""
Tokenixo - Offline Token Counter
=================================
A standalone window app to count tokens. Real-time counting as you type.
Author: Duzf7 | License: MIT | https://github.com/Duzf7/Tokenixo
Dependencies: pip install tiktoken  OR  pip install tokenizers
"""
VERSION = "26.1.6"  # ← change version here; syncs to title bar and credit label

import sys, platform, subprocess, threading, tkinter as tk
from tkinter import scrolledtext, filedialog
from bisect import bisect_left, bisect_right


def _load_tiktoken():
    import tiktoken  # type: ignore[reportMissingImports]
    enc = tiktoken.get_encoding("cl100k_base")
    _ds = lambda d: sys.getsizeof(d) + sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in d.items())
    size = _ds(enc._mergeable_ranks) + (_ds(enc._special_tokens) if hasattr(enc, '_special_tokens') else 0)
    def encode(text): return enc.encode(text)
    def token_spans(text):
        spans, offset = [], 0
        for tok_id in enc.encode(text):
            piece = enc.decode([tok_id]); spans.append((offset, offset + len(piece))); offset += len(piece)
        return spans
    return encode, token_spans, size


def _load_xenova():
    from transformers import GPT2TokenizerFast  # type: ignore[reportMissingImports]
    try:    tok = GPT2TokenizerFast.from_pretrained('Xenova/claude-tokenizer', local_files_only=True)
    except: tok = GPT2TokenizerFast.from_pretrained('Xenova/claude-tokenizer')
    try:    size = len(tok.backend_tokenizer.to_str().encode('utf-8'))
    except: size = sys.getsizeof(tok.get_vocab()) + sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in tok.get_vocab().items())
    def encode(text): return tok.encode(text)
    def token_spans(text):
        encoding = tok(text, return_offsets_mapping=True)
        offsets = encoding.get("offset_mapping", [])
        if offsets: return [(s, e) for s, e in offsets if e > s]
        spans, offset = [], 0
        for tok_id in encoding["input_ids"]:
            piece = tok.decode([tok_id]); spans.append((offset, offset + len(piece))); offset += len(piece)
        return spans
    return encode, token_spans, size


TOKENIZER_LOADERS = [("tiktoken (cl100k_base)", _load_tiktoken), ("Xenova/claude-tokenizer", _load_xenova)]


def _build_line_index(text):
    starts, pos = [0], 0
    while True:
        pos = text.find('\n', pos)
        if pos == -1: break
        pos += 1; starts.append(pos)
    return starts


def _batch_offsets_to_linecol(offsets, line_starts):
    results, line_idx, num_lines = [], 0, len(line_starts)
    for off in offsets:
        while line_idx + 1 < num_lines and line_starts[line_idx + 1] <= off: line_idx += 1
        results.append(f"{line_idx + 1}.{off - line_starts[line_idx]}")
    return results


_TAG_NAMES      = [f"tok{i}"  for i in range(6)]
_TAG_NAMES_BOLD = [f"tok{i}b" for i in range(6)]
_NUM_COLORS     = 6

_LIGHT_SOFT = ["#EBF0FE","#FEF8E3","#E6FCF1","#FDF1F8","#EEEFFF","#FEF1C4"]
_LIGHT_BOLD = ["#B8D4FD","#FDE69A","#A8F0CE","#F9C8E1","#C4CCFF","#FCD650"]
_DARK_SOFT  = ["#1a3154","#2e2800","#0a2a18","#2e0e20","#1c1a3c","#2e2300"]
_DARK_BOLD  = ["#1e5ca8","#7a6200","#0d7a3c","#7a1e5c","#2e2e9e","#7a6200"]
_LIGHT_BG, _LIGHT_FG = "#ffffff", "#000000"
_DARK_BG,  _DARK_FG  = "#1e1e1e", "#d4d4d4"


def _is_dark_mode():
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
            return r.returncode == 0 and r.stdout.strip() == "Dark"
    except Exception: pass
    return False


class TokenCounterApp:
    def __init__(self):
        (self.tokenizers, self.method, self.encode_func, self.spans_func,
         self._loading, self._has_counted, self._full_results) = {}, None, None, None, True, False, None
        (self._cached_spans, self._cached_span_starts, self._cached_line_starts,
         self._last_text, self._count_gen, self._visible_tag_range) = None, None, None, None, 0, None
        self._recount_after_id = self._scroll_after_id = None
        self._dark_mode = _is_dark_mode()

        self.root = tk.Tk()
        self.root.title(f"Tokenixo v{VERSION} - Offline Token Counter")
        w, h = 1000, 750
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.root.minsize(700, 500)
        self._build_ui()
        self._apply_theme()
        self._poll_dark_mode()
        threading.Thread(target=self._load_tokenizers, daemon=True).start()

    def _load_tokenizers(self):
        for name, loader in TOKENIZER_LOADERS:
            try:
                enc, spn, sz = loader()
                self.tokenizers[name] = (enc, spn, sz)
            except Exception: pass
        self.root.after(0, self._on_tokenizers_ready)

    def _on_tokenizers_ready(self):
        self._loading = False
        if not self.tokenizers:
            self.status_label.config(text="ERROR: No tokenizer found. pip install tiktoken or transformers"); return
        self.method = list(self.tokenizers.keys())[0]
        self.encode_func, self.spans_func, _ = self.tokenizers[self.method]
        menu = self.method_menu["menu"]; menu.delete(0, "end")
        for name in self.tokenizers:
            _, _, sz = self.tokenizers[name]
            menu.add_command(label=f"{name} — {sz/(1024*1024):.2f} MB", command=lambda n=name: self._on_method_change(n))
        _, _, sz = self.tokenizers[self.method]
        self.method_var.set(f"{self.method} — {sz/(1024*1024):.2f} MB")
        self.status_label.config(text=f"Ready — {len(self.tokenizers)} tokenizer(s)")
        text = self.text_input.get("1.0", "end-1c")
        if text.strip(): self._count()

    def _build_ui(self):
        root = self.root
        top = tk.Frame(root); top.pack(fill=tk.X, padx=10, pady=(10,5))
        tk.Label(top, text="Tokenizer:", anchor="w").pack(side=tk.LEFT)
        self.method_var = tk.StringVar(value="Loading...")
        self.method_menu = tk.OptionMenu(top, self.method_var, "Loading...")
        self.method_menu.pack(side=tk.LEFT, padx=(5,0))
        tk.Button(top, text="Open File...", command=self._open_file).pack(side=tk.RIGHT, padx=(5,0))
        tk.Button(top, text="Clear", command=self._clear).pack(side=tk.RIGHT, padx=(5,0))
        self.status_label = tk.Label(root, text="Loading tokenizers...", anchor="w", fg="gray")
        self.status_label.pack(fill=tk.X, padx=10)
        tk.Label(root, text="Paste or type your text:", anchor="w").pack(fill=tk.X, padx=10)
        self.text_input = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Menlo", 13))
        self.text_input.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,5))
        for tag in _TAG_NAMES:      self.text_input.tag_configure(tag)
        for tag in _TAG_NAMES_BOLD: self.text_input.tag_configure(tag); self.text_input.tag_raise(tag)
        def _on_yscroll(*a): self.text_input.vbar.set(*a); self._schedule_viewport_update()
        self.text_input.configure(yscrollcommand=_on_yscroll)
        self.text_input.bind("<Configure>", lambda e: self._schedule_viewport_update())

        self.results_frame = tk.LabelFrame(root, text="Results", padx=10, pady=5)
        self.results_frame.pack(fill=tk.X, padx=10, pady=(0,10))
        self.result_labels = {}
        row = tk.Frame(self.results_frame); row.pack(fill=tk.X)
        for dname, key in [("Tokens","tokens"),("Characters","characters"),("Words","words"),("Lines","lines"),("Chars/Token","cpt")]:
            cell = tk.Frame(row); cell.pack(side=tk.LEFT, expand=True, fill=tk.X)
            tk.Label(cell, text=dname, font=("Helvetica",11)).pack()
            lbl = tk.Label(cell, text="-", font=("Helvetica",16,"bold")); lbl.pack()
            self.result_labels[key] = lbl
        tk.Label(self.results_frame, text="Context Window Usage:", anchor="w", font=("Helvetica",11)).pack(fill=tk.X, pady=(8,2))
        self.context_labels = {}
        for name in ["Haiku 4.5","Sonnet 4.6","Opus 4.6"]:
            lbl = tk.Label(self.results_frame, text="", anchor="w"); lbl.pack(fill=tk.X); self.context_labels[name] = lbl
        tk.Label(root, text=f"Tokenixo v{VERSION}  |  By Duzf7  |  MIT License",
                 anchor="center", fg="gray", font=("Helvetica",10)).pack(fill=tk.X, padx=10, pady=(0,5))

        ti = self.text_input
        ti.bind("<KeyRelease>",      lambda e: self._schedule_recount())
        ti.bind("<<Paste>>",         lambda e: self.root.after(1, self._schedule_recount))
        ti.bind("<<Cut>>",           lambda e: self.root.after(1, self._schedule_recount))
        ti.bind("<<Selection>>",     lambda e: self._on_selection_change())
        ti.bind("<ButtonRelease-1>", lambda e: self.root.after(10, self._on_selection_change))

    def _apply_theme(self):
        dark = self._dark_mode
        soft, bold = (_DARK_SOFT, _DARK_BOLD) if dark else (_LIGHT_SOFT, _LIGHT_BOLD)
        bg, fg = (_DARK_BG, _DARK_FG) if dark else (_LIGHT_BG, _LIGHT_FG)
        self.text_input.configure(background=bg, foreground=fg, insertbackground=fg, selectbackground=bg, selectforeground=fg)
        for tag, c in zip(_TAG_NAMES, soft):      self.text_input.tag_configure(tag, background=c)
        for tag, c in zip(_TAG_NAMES_BOLD, bold): self.text_input.tag_configure(tag, background=c)
        self._visible_tag_range = None; self._apply_visible_highlights()

    def _poll_dark_mode(self):
        cur = _is_dark_mode()
        if cur != self._dark_mode: self._dark_mode = cur; self._apply_theme()
        self.root.after(2000, self._poll_dark_mode)

    def _schedule_recount(self):
        if self._recount_after_id is not None: self.root.after_cancel(self._recount_after_id)
        self._recount_after_id = self.root.after(150, lambda: self._count() if self.text_input.get("1.0","end-1c") != self._last_text else None)

    def _schedule_viewport_update(self):
        if self._scroll_after_id is not None: self.root.after_cancel(self._scroll_after_id)
        self._scroll_after_id = self.root.after(16, self._apply_visible_highlights)

    def _on_method_change(self, selection):
        self.method = selection
        self.encode_func, self.spans_func, _ = self.tokenizers[selection]
        _, _, sz = self.tokenizers[self.method]; self.method_var.set(f"{self.method} — {sz/(1024*1024):.2f} MB")
        self._has_counted = False; self._full_results = None; self._count()

    def _apply_visible_highlights(self):
        if not self._cached_spans or not self._cached_line_starts: return
        ti, ls = self.text_input, self._cached_line_starts
        spans, span_starts = self._cached_spans, self._cached_span_starts
        first_idx_str = ti.index("@0,0"); last_idx_str = ti.index(f"@{ti.winfo_width()},{ti.winfo_height()}")
        fl, fc = map(int, first_idx_str.split('.')); ll, lc = map(int, last_idx_str.split('.'))
        vis_start = ls[min(fl-1, len(ls)-1)] + fc; vis_end = ls[min(ll-1, len(ls)-1)] + lc
        margin = 200
        fi = max(0, bisect_left(span_starts, vis_start) - margin)
        li = min(len(spans), bisect_right(span_starts, vis_end) + margin)
        if (fi, li) == self._visible_tag_range: return
        self._visible_tag_range = (fi, li)
        for tag in _TAG_NAMES: ti.tag_remove(tag, "1.0", tk.END)
        visible = spans[fi:li]
        if not visible: return
        lc_list = _batch_offsets_to_linecol([v for s, e in visible for v in (s, e)], ls)
        batches = [[] for _ in range(_NUM_COLORS)]
        for j in range(len(visible)): batches[(fi+j) % _NUM_COLORS].append((lc_list[2*j], lc_list[2*j+1]))
        for ci, batch in enumerate(batches):
            if batch: ti.tk.call(ti._w, "tag", "add", _TAG_NAMES[ci], *[v for s, e in batch for v in (s, e)])

    def _apply_bold_to_range(self, s_off, e_off):
        if not self._cached_spans or not self._cached_line_starts: return
        ti, ls = self.text_input, self._cached_line_starts
        spans, span_starts = self._cached_spans, self._cached_span_starts
        first = max(0, bisect_left(span_starts, s_off) - 1)
        last  = bisect_right(span_starts, e_off)
        flat, indices = [], []
        for i in range(first, min(last, len(spans))):
            s, e = spans[i]
            if e <= s_off or s >= e_off: continue
            flat += [max(s, s_off), min(e, e_off)]; indices.append(i)
        if not flat: return
        lc_list = _batch_offsets_to_linecol(flat, ls)
        batches = [[] for _ in range(_NUM_COLORS)]
        for j, idx in enumerate(indices): batches[idx % _NUM_COLORS].append((lc_list[2*j], lc_list[2*j+1]))
        for ci, batch in enumerate(batches):
            if batch: ti.tk.call(ti._w, "tag", "add", _TAG_NAMES_BOLD[ci], *[v for s, e in batch for v in (s, e)])

    def _on_selection_change(self):
        if not self._has_counted or self.encode_func is None: return
        for tag in _TAG_NAMES_BOLD: self.text_input.tag_remove(tag, "1.0", tk.END)
        try:
            sel_text = self.text_input.get(tk.SEL_FIRST, tk.SEL_LAST)
            content  = self.text_input.get("1.0", "end-1c")
            if len(sel_text) > len(content): sel_text = content
        except tk.TclError:
            if self._full_results: self._update_display(*self._full_results, label="Results")
            return
        if not sel_text.strip():
            if self._full_results: self._update_display(*self._full_results, label="Results")
            return
        if self._cached_line_starts:
            sl, sc = map(int, self.text_input.index(tk.SEL_FIRST).split('.'))
            el, ec = map(int, self.text_input.index(tk.SEL_LAST).split('.'))
            self._apply_bold_to_range(self._cached_line_starts[sl-1]+sc, self._cached_line_starts[el-1]+ec)
        t = len(self.encode_func(sel_text)); c = len(sel_text)
        self._update_display(t, c, len(sel_text.split()), sel_text.count('\n')+(1 if sel_text else 0),
                             round(c/t, 2) if t else 0, label="Results (selection)")

    def _update_display(self, tokens, chars, words, lines, cpt, label="Results"):
        self.results_frame.config(text=label)
        for key, val in [("tokens",f"{tokens:,}"),("characters",f"{chars:,}"),("words",f"{words:,}"),("lines",f"{lines:,}"),("cpt",f"{cpt}")]:
            self.result_labels[key].config(text=val)
        for name, limit in [("Haiku 4.5",200_000),("Sonnet 4.6",1_000_000),("Opus 4.6",1_000_000)]:
            self.context_labels[name].config(text=f"  {name:12s}  {tokens:>10,} / {limit:>10,}  ({tokens/limit*100:.4f}%)")

    def _remove_highlights(self):
        for tag in _TAG_NAMES + _TAG_NAMES_BOLD: self.text_input.tag_remove(tag, "1.0", tk.END)
        self._visible_tag_range = None

    def _reset_state(self, clear_text=False):
        self._has_counted = False; self._full_results = None
        self._cached_spans = self._cached_span_starts = self._cached_line_starts = None
        self.results_frame.config(text="Results")
        for lbl in self.result_labels.values():  lbl.config(text="-")
        for lbl in self.context_labels.values(): lbl.config(text="")
        if clear_text: self._last_text = None; self.text_input.delete("1.0", tk.END)

    def _count(self):
        if self._loading or self.encode_func is None: return
        text = self.text_input.get("1.0", "end-1c")
        if not text.strip():
            self._remove_highlights(); self._last_text = text; self._reset_state(); return
        self._count_gen += 1
        gen, spans_func = self._count_gen, self.spans_func
        def worker():
            try:    spans = spans_func(text)
            except: spans = None
            if not spans:
                self.root.after(0, lambda: self._on_count_done(gen, text, None, None, None)); return
            ls = _build_line_index(text); ss = [s for s, _ in spans]
            self.root.after(0, lambda: self._on_count_done(gen, text, spans, ss, ls))
        threading.Thread(target=worker, daemon=True).start()

    def _on_count_done(self, gen, text, spans, span_starts, line_starts):
        if gen != self._count_gen: return
        self._cached_spans, self._cached_span_starts = spans, span_starts
        self._cached_line_starts, self._last_text = line_starts, text
        self._visible_tag_range = None
        if not spans:
            self._remove_highlights(); self._has_counted = False; self._full_results = None; return
        t = len(spans); c = len(text)
        self._full_results = (t, c, len(text.split()), text.count('\n')+(1 if text else 0), round(c/t,2) if t else 0)
        self._has_counted = True
        self._remove_highlights(); self._apply_visible_highlights()
        self._update_display(*self._full_results, label="Results")

    def _clear(self):
        self._count_gen += 1; self._remove_highlights(); self._reset_state(clear_text=True)

    def _open_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files","*.txt *.md *.py *.js *.ts *.json *.csv"),("All files","*.*")])
        if path:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f: content = f.read()
                self.text_input.delete("1.0", tk.END); self.text_input.insert("1.0", content); self._count()
            except Exception as e:
                self.text_input.delete("1.0", tk.END); self.text_input.insert("1.0", f"Error reading file: {e}")

    def run(self): self.root.mainloop()


if __name__ == "__main__":
    TokenCounterApp().run()
