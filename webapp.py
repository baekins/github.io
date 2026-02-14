"""Polybet – Polymarket 스포츠 베팅 분석기 (tkinter GUI)"""
from __future__ import annotations


import asyncio
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import sys
import os


# ── 패키지 경로 ──────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from polybet.analysis import analyze

# ── 색상 테마 ──────────────────────────────────────────────
BG      = "#1a1a2e"
BG2     = "#16213e"
BG3     = "#0f3460"
FG      = "#e0e0e0"
FG2     = "#a0a0b0"
ACCENT  = "#e94560"
GREEN   = "#00c853"
ORANGE  = "#ff9800"
ENTRY_BG = "#22264b"
BTN_BG  = "#e94560"
BTN_FG  = "#ffffff"

# ── API 키 저장 경로 ──────────────────────────────────────
API_KEY_FILE = os.path.join(ROOT, ".api_key")

# ── 예시 URL ───────────────────────────────────────────────
EXAMPLES = [
    ("맨시티 vs 살포드", "https://polymarket.com/sports/fa-cup/efa-mnc-sal2-2026-02-14"),
    ("NBA 오늘", "https://polymarket.com/sports/nba"),
    ("UFC", "https://polymarket.com/sports/mma"),
]


class PolyBetApp:
    """메인 GUI 클래스"""
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PolyBet – 스포츠 베팅 분석기")
        self.root.geometry("860x780")
        self.root.configure(bg=BG)
        self.root.minsize(700, 600)

        # 스타일 설정
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("맑은 고딕", 10))
        style.configure("Title.TLabel", background=BG, foreground="#ffffff",
                         font=("맑은 고딕", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=FG2, font=("맑은 고딕", 9))
        style.configure("Accent.TButton", background=BTN_BG, foreground=BTN_FG,
                         font=("맑은 고딕", 11, "bold"), padding=(20, 8))
        style.map("Accent.TButton",
                   background=[("active", "#c73652"), ("disabled", "#555")])
        style.configure("Chip.TButton", background=BG3, foreground=FG,
                         font=("맑은 고딕", 9), padding=(10, 4))
        style.map("Chip.TButton", background=[("active", "#1a4a7a")])

        self._build_ui()
        self._running = False
        self._load_api_key()

    # ── API 키 로드/저장 ──────────────────────────────────
    def _load_api_key(self):
        try:
            if os.path.exists(API_KEY_FILE):
                with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                    key = f.read().strip()
                    if key:
                        self.api_var.set(key)
        except Exception:
            pass

    def _save_api_key(self):
        try:
            key = self.api_var.get().strip()
            with open(API_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(key)
        except Exception:
            pass

    # ── UI 구성 ────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 16, "pady": 4}

        # 헤더
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=16, pady=(16, 4))
        ttk.Label(hdr, text="PolyBet", style="Title.TLabel").pack(side="left")
        ttk.Label(hdr, text="Polymarket 실시간 베팅 분석",
                  style="Sub.TLabel").pack(side="left", padx=(12, 0))

        # 상태 배지
        self.badge_var = tk.StringVar(value="")
        self.badge = tk.Label(hdr, textvariable=self.badge_var, bg=BG, fg=GREEN,
                              font=("맑은 고딕", 11, "bold"))
        self.badge.pack(side="right")

        # 구분선
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=16, pady=4)

        # Claude API 키 입력
        api_frame = ttk.Frame(self.root)
        api_frame.pack(fill="x", padx=16, pady=(4, 2))
        ttk.Label(api_frame, text="Claude API 키 (실시간 AI 분석용):",
                  style="Sub.TLabel").pack(side="left")
        self.api_var = tk.StringVar()
        self.api_entry = tk.Entry(api_frame, textvariable=self.api_var,
                                   bg=ENTRY_BG, fg="#888888",
                                   insertbackground=FG, font=("Consolas", 9),
                                   relief="flat", bd=0, show="*",
                                   highlightthickness=1,
                                   highlightcolor=BG3,
                                   highlightbackground="#333")
        self.api_entry.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

        # 보기/숨기기 토글
        self._api_visible = False
        self.toggle_btn = tk.Button(api_frame, text="보기", bg=BG3, fg=FG2,
                                     font=("맑은 고딕", 8), relief="flat",
                                     command=self._toggle_api_visibility,
                                     cursor="hand2")
        self.toggle_btn.pack(side="right", padx=(4, 0))

        # 입력 영역
        inp_frame = ttk.Frame(self.root)
        inp_frame.pack(fill="x", padx=16, pady=(8, 2))
        ttk.Label(inp_frame, text="마켓 URL 또는 슬러그:").pack(anchor="w")
        row = ttk.Frame(inp_frame)
        row.pack(fill="x", pady=(4, 0))

        self.url_var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.url_var,
                              bg=ENTRY_BG, fg=FG, insertbackground=FG,
                              font=("Consolas", 11), relief="flat", bd=0,
                              highlightthickness=1, highlightcolor=ACCENT,
                              highlightbackground="#333")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self._on_analyze())

        self.btn = ttk.Button(row, text="분석 시작",
                              style="Accent.TButton",
                              command=self._on_analyze)
        self.btn.pack(side="right", padx=(8, 0))

        # 예시 칩
        chip_frame = ttk.Frame(self.root)
        chip_frame.pack(fill="x", padx=16, pady=(2, 8))
        ttk.Label(chip_frame, text="예시:", style="Sub.TLabel").pack(side="left")
        for label, url in EXAMPLES:
            b = ttk.Button(chip_frame, text=label, style="Chip.TButton",
                           command=lambda u=url: self._set_url(u))
            b.pack(side="left", padx=(6, 0))

        # 참고 배당률 입력 (접이식)
        ref_frame = ttk.Frame(self.root)
        ref_frame.pack(fill="x", padx=16, pady=(0, 4))
        ttk.Label(ref_frame,
                  text="참고 배당률 (선택사항 — 예: 맨시티: 1.25)",
                  style="Sub.TLabel").pack(anchor="w")
        self.ref_text = tk.Text(ref_frame, height=2, bg=ENTRY_BG, fg=FG,
                                insertbackground=FG, font=("Consolas", 10),
                                relief="flat", bd=0,
                                highlightthickness=1,
                                highlightcolor=BG3,
                                highlightbackground="#333")
        self.ref_text.pack(fill="x", pady=(2, 0))

        # 결과 영역
        res_frame = ttk.Frame(self.root)
        res_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        ttk.Label(res_frame, text="분석 결과:").pack(anchor="w")
        self.result = scrolledtext.ScrolledText(
            res_frame, bg="#0d1117", fg="#c9d1d9",
            insertbackground=FG, font=("Consolas", 10),
            relief="flat", bd=0, wrap="word", state="disabled",
            highlightthickness=1, highlightbackground="#333"
        )
        self.result.pack(fill="both", expand=True, pady=(4, 0))

        # 하단 상태바
        self.status_var = tk.StringVar(value="준비 완료")
        status = tk.Label(self.root, textvariable=self.status_var,
                          bg=BG2, fg=FG2, font=("맑은 고딕", 9),
                          anchor="w", padx=16)
        status.pack(fill="x", side="bottom")

    # ── API 키 보기/숨기기 ─────────────────────────────────
    def _toggle_api_visibility(self):
        self._api_visible = not self._api_visible
        if self._api_visible:
            self.api_entry.configure(show="")
            self.toggle_btn.configure(text="숨기기")
        else:
            self.api_entry.configure(show="*")
            self.toggle_btn.configure(text="보기")

    # ── 동작 ───────────────────────────────────────────────
    def _set_url(self, url: str):
        self.url_var.set(url)
        self.entry.focus_set()

    def _on_analyze(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("입력 필요", "Polymarket URL을 입력하세요.")
            return
        if self._running:
            return

        self._running = True
        self.btn.configure(state="disabled")
        self.badge_var.set("")
        self.badge.configure(fg=GREEN)

        api_key = self.api_var.get().strip()
        # API 키 저장
        self._save_api_key()

        progress_msg = "분석 중... 잠시 기다려주세요\n\n"
        progress_msg += "Polymarket API에서 데이터를 가져오고 있습니다...\n"
        if api_key:
            progress_msg += "Claude AI가 실시간 정보를 검색하고 있습니다...\n"
        self._set_result(progress_msg)
        self.status_var.set("분석 진행 중..." + (" (AI 분석 포함)" if api_key else ""))

        ref = self.ref_text.get("1.0", "end").strip()
        thread = threading.Thread(target=self._run_analysis,
                                  args=(url, ref, api_key), daemon=True)
        thread.start()

    def _run_analysis(self, url: str, ref: str, api_key: str):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(analyze(url, ref, api_key=api_key))
            loop.close()

            # 결과에서 추천/패스 판단
            if "추천" in result or "매수" in result:
                badge_text, badge_color = "추천", GREEN
            elif "패스" in result or "보류" in result:
                badge_text, badge_color = "패스", ACCENT
            else:
                badge_text, badge_color = "완료", ORANGE

            self.root.after(0, self._show_result, result, badge_text, badge_color)
        except Exception as e:
            err_msg = f"오류 발생: {e}\n\n다시 시도해주세요."
            self.root.after(0, self._show_result, err_msg, "오류", ACCENT)

    def _show_result(self, text: str, badge: str, color: str):
        self._set_result(text)
        self.badge_var.set(badge)
        self.badge.configure(fg=color)
        self.btn.configure(state="normal")
        self.status_var.set("분석 완료")
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
