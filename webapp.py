"""Polybet â Polymarket ì¤í¬ì¸  ë² í ë¶ìê¸° (tkinter GUI)"""
from __future__ import annotations

import asyncio
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import sys
import os

# ââ í¨í¤ì§ ê²½ë¡ ââââââââââââââââââââââââââââââââââââââââââ
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from polybet.analysis import analyze

# ââ ìì íë§ ââââââââââââââââââââââââââââââââââââââââââââ
BG       = "#1a1a2e"
BG2      = "#16213e"
BG3      = "#0f3460"
FG       = "#e0e0e0"
FG2      = "#a0a0b0"
ACCENT   = "#e94560"
GREEN    = "#00c853"
ORANGE   = "#ff9800"
ENTRY_BG = "#22264b"
BTN_BG   = "#e94560"
BTN_FG   = "#ffffff"

# ââ ìì URL âââââââââââââââââââââââââââââââââââââââââââââ
EXAMPLES = [
    ("ë§¨ìí° vs ì´í¬ë", "https://polymarket.com/sports/fa-cup/efa-mnc-sal2-2026-02-14"),
    ("NBA ì¤ë", "https://polymarket.com/sports/nba"),
    ("UFC", "https://polymarket.com/sports/mma"),
]


class PolyBetApp:
    """ë©ì¸ GUI í´ëì¤"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PolyBet â ì¤í¬ì¸  ë² í ë¶ìê¸°")
        self.root.geometry("860x720")
        self.root.configure(bg=BG)
        self.root.minsize(700, 550)

        # ì¤íì¼ ì¤ì 
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("ë§ì ê³ ë", 10))
        style.configure("Title.TLabel", background=BG, foreground="#ffffff",
                        font=("ë§ì ê³ ë", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=FG2,
                        font=("ë§ì ê³ ë", 9))
        style.configure("Accent.TButton", background=BTN_BG, foreground=BTN_FG,
                        font=("ë§ì ê³ ë", 11, "bold"), padding=(20, 8))
        style.map("Accent.TButton",
                  background=[("active", "#c73652"), ("disabled", "#555")])
        style.configure("Chip.TButton", background=BG3, foreground=FG,
                        font=("ë§ì ê³ ë", 9), padding=(10, 4))
        style.map("Chip.TButton", background=[("active", "#1a4a7a")])

        self._build_ui()
        self._running = False

    # ââ UI êµ¬ì± ââââââââââââââââââââââââââââââââââââââââââ
    def _build_ui(self):
        pad = {"padx": 16, "pady": 4}

        # í¤ë
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=16, pady=(16, 4))
        ttk.Label(hdr, text="PolyBet", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text="Polymarket ì¤ìê° ë² í ë¶ì",
                  style="Sub.TLabel").pack(side="left", padx=(12, 0))

        # ìí ë°°ì§
        self.badge_var = tk.StringVar(value="")
        self.badge = tk.Label(hdr, textvariable=self.badge_var, bg=BG,
                              fg=GREEN, font=("ë§ì ê³ ë", 11, "bold"))
        self.badge.pack(side="right")

        # êµ¬ë¶ì 
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=16, pady=4)

        # ìë ¥ ìì­
        inp_frame = ttk.Frame(self.root)
        inp_frame.pack(fill="x", padx=16, pady=(8, 2))
        ttk.Label(inp_frame, text="ë§ì¼ URL ëë ì¬ë¬ê·¸:").pack(anchor="w")

        row = ttk.Frame(inp_frame)
        row.pack(fill="x", pady=(4, 0))

        self.url_var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.url_var, bg=ENTRY_BG,
                              fg=FG, insertbackground=FG, font=("Consolas", 11),
                              relief="flat", bd=0, highlightthickness=1,
                              highlightcolor=ACCENT, highlightbackground="#333")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self._on_analyze())

        self.btn = ttk.Button(row, text="ë¶ì ìì", style="Accent.TButton",
                              command=self._on_analyze)
        self.btn.pack(side="right", padx=(8, 0))

        # ìì ì¹©
        chip_frame = ttk.Frame(self.root)
        chip_frame.pack(fill="x", padx=16, pady=(2, 8))
        ttk.Label(chip_frame, text="ìì:", style="Sub.TLabel").pack(side="left")
        for label, url in EXAMPLES:
            b = ttk.Button(chip_frame, text=label, style="Chip.TButton",
                           command=lambda u=url: self._set_url(u))
            b.pack(side="left", padx=(6, 0))

        # ì°¸ê³  ë°°ë¹ë¥  ìë ¥ (ì ì´ì)
        ref_frame = ttk.Frame(self.root)
        ref_frame.pack(fill="x", padx=16, pady=(0, 4))
        ttk.Label(ref_frame, text="ì°¸ê³  ë°°ë¹ë¥  (ì íì¬í­ â ì: ë§¨ìí°: 1.25)",
                  style="Sub.TLabel").pack(anchor="w")
        self.ref_text = tk.Text(ref_frame, height=2, bg=ENTRY_BG, fg=FG,
                                insertbackground=FG, font=("Consolas", 10),
                                relief="flat", bd=0, highlightthickness=1,
                                highlightcolor=BG3, highlightbackground="#333")
        self.ref_text.pack(fill="x", pady=(2, 0))

        # ê²°ê³¼ ìì­
        res_frame = ttk.Frame(self.root)
        res_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        ttk.Label(res_frame, text="ë¶ì ê²°ê³¼:").pack(anchor="w")

        self.result = scrolledtext.ScrolledText(
            res_frame, bg="#0d1117", fg="#c9d1d9",
            insertbackground=FG, font=("Consolas", 10),
            relief="flat", bd=0, wrap="word", state="disabled",
            highlightthickness=1, highlightbackground="#333"
        )
        self.result.pack(fill="both", expand=True, pady=(4, 0))

        # íë¨ ìíë°
        self.status_var = tk.StringVar(value="ì¤ë¹ ìë£")
        status = tk.Label(self.root, textvariable=self.status_var, bg=BG2,
                          fg=FG2, font=("ë§ì ê³ ë", 9), anchor="w", padx=16)
        status.pack(fill="x", side="bottom")

    # ââ ëì âââââââââââââââââââââââââââââââââââââââââââââ
    def _set_url(self, url: str):
        self.url_var.set(url)
        self.entry.focus_set()

    def _on_analyze(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("ìë ¥ íì", "Polymarket URLì ìë ¥íì¸ì.")
            return
        if self._running:
            return

        self._running = True
        self.btn.configure(state="disabled")
        self.badge_var.set("")
        self.badge.configure(fg=GREEN)
        self._set_result("ë¶ì ì¤... ì ì ê¸°ë¤ë ¤ì£¼ì¸ì â³\n\n"
                         "Polymarket APIìì ë°ì´í°ë¥¼ ê°ì ¸ì¤ê³  ììµëë¤...")
        self.status_var.set("ë¶ì ì§í ì¤...")

        ref = self.ref_text.get("1.0", "end").strip()
        thread = threading.Thread(target=self._run_analysis,
                                  args=(url, ref), daemon=True)
        thread.start()

    def _run_analysis(self, url: str, ref: str):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(analyze(url, ref))
            loop.close()

            # ê²°ê³¼ìì ì¶ì²/í¨ì¤ íë¨
            if "ì¶ì²" in result or "ë§¤ì" in result:
                badge_text, badge_color = "ì¶ì²", GREEN
            elif "í¨ì¤" in result or "ë³´ë¥" in result:
                badge_text, badge_color = "í¨ì¤", ACCENT
            else:
                badge_text, badge_color = "ìë£", ORANGE

            self.root.after(0, self._show_result, result, badge_text, badge_color)
        except Exception as e:
            err_msg = f"ì¤ë¥ ë°ì: {e}\n\në¤ì ìëí´ì£¼ì¸ì."
            self.root.after(0, self._show_result, err_msg, "ì¤ë¥", ACCENT)

    def _show_result(self, text: str, badge: str, color: str):
        self._set_result(text)
        self.badge_var.set(badge)
        self.badge.configure(fg=color)
        self.btn.configure(state="normal")
        self.status_var.set("ë¶ì ìë£")
        self._running = False

    def _set_result(self, text: str):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")


def main():
    root = tk.Tk()
    root.iconname("PolyBet")
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = PolyBetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
