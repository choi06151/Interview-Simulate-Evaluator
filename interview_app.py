from __future__ import annotations

import calendar as month_calendar
import json
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk

from local_assistant import MODEL_NAME, analyze_answer, list_local_models
from live_audio import LiveAudioCapture
from speaker_diarization import diarize_audio_samples, reset_diarization

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "records" / "participants"
QUESTION_SETS = ROOT / "question-library" / "daily"
BG = "#f3f5f8"
PANEL = "#ffffff"
INK = "#172033"
MUTED = "#687386"
ACCENT = "#315efb"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "이 파일을 읽을 수 없습니다."


def meta_value(text: str, key: str, fallback: str = "") -> str:
    match = re.search(rf"^-\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.M)
    return match.group(1).strip() if match else fallback


def extract_score(text: str, label: str) -> str:
    match = re.search(rf"\*\*{re.escape(label)}:\s*([^*]+)\*\*", text)
    if match:
        return match.group(1).strip()
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n|]+)", text)
    return match.group(1).strip() if match else "기록 없음"


def participant_name(participant_dir: Path) -> str:
    profile = read_text(participant_dir / "PROFILE.md")
    name = meta_value(profile, "표시 이름")
    if name:
        return name
    for transcript in sorted((participant_dir / "sessions").glob("*/transcript.md"), reverse=True):
        name = meta_value(read_text(transcript), "표시 이름")
        if name:
            return name
    return participant_dir.name


def list_participants() -> dict[str, str]:
    if not RECORDS.exists():
        return {}
    return {
        folder.name: participant_name(folder)
        for folder in RECORDS.iterdir()
        if folder.is_dir()
    }


def find_sessions() -> list[dict[str, object]]:
    sessions: list[dict[str, object]] = []
    if not RECORDS.exists():
        return sessions
    for person_dir in sorted(RECORDS.iterdir()):
        session_root = person_dir / "sessions"
        if not session_root.is_dir():
            continue
        for folder in session_root.iterdir():
            if not folder.is_dir():
                continue
            transcript_path = folder / "transcript.md"
            evaluation_path = folder / "evaluation.md"
            transcript = read_text(transcript_path) if transcript_path.exists() else ""
            evaluation = read_text(evaluation_path) if evaluation_path.exists() else ""
            session_id = meta_value(transcript, "세션 ID", folder.name)
            date = meta_value(transcript, "기록일", session_id)
            interview_type = meta_value(transcript, "면접 유형")
            try:
                sort_date = datetime.strptime(date[:10], "%Y-%m-%d")
            except ValueError:
                sort_date = datetime.min
            audio_files = [p for p in folder.iterdir() if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".flac", ".ogg"}]
            rec_dir = person_dir / "question-recommendations"
            rec_path = rec_dir / f"after-{session_id}.md"
            sessions.append({
                "participant_id": person_dir.name,
                "participant_name": participant_name(person_dir),
                "session_id": session_id,
                "date": date,
                "interview_type": interview_type,
                "sort_date": sort_date,
                "folder": folder,
                "transcript": transcript,
                "evaluation": evaluation,
                "assistance": read_text(folder / "live-assistance.md") if (folder / "live-assistance.md").exists() else "이 회차에 저장된 실시간 조언이 없습니다.",
                "audio_files": audio_files,
                "recommendation": read_text(rec_path) if rec_path.exists() else "이 회차에 연결된 추천 질문 파일이 없습니다.",
                "text_score": extract_score(evaluation, "텍스트 점수"),
                "voice_score": extract_score(evaluation, "음성 점수"),
                "total_score": extract_score(evaluation, "최종 총점"),
            })
    chronological_by_person: dict[str, list[dict[str, object]]] = {}
    for session in sessions:
        chronological_by_person.setdefault(str(session["participant_id"]), []).append(session)
    for person_sessions in chronological_by_person.values():
        person_sessions.sort(key=lambda item: (item["sort_date"], item["session_id"]))
        for ordinal, session in enumerate(person_sessions, start=1):
            saved_round = meta_value(str(session["transcript"]), "면접 회차")
            if not saved_round:
                round_match = re.search(r"(\d+)\s*회차", str(session["interview_type"]))
                saved_round = f"{round_match.group(1)}회차" if round_match else f"{ordinal}회차"
            round_match = re.search(r"(\d+)\s*회차", saved_round)
            session["round_label"] = saved_round
            session["round_number"] = int(round_match.group(1)) if round_match else ordinal
    return sorted(sessions, key=lambda item: (
        str(item["participant_name"]).casefold(),
        int(item["round_number"]),
        item["sort_date"],
        str(item["session_id"]),
    ))


class InterviewApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("모의 면접 기록실")
        self.geometry("1240x800")
        self.minsize(980, 640)
        self.configure(bg=BG)
        self.sessions = find_sessions()
        self.visible_sessions: list[dict[str, object]] = []
        self.live_capture: LiveAudioCapture | None = None
        self.live_chunks: list[dict[str, object]] = []
        self.diarization_turns: list[dict[str, object]] = []
        self.diarization_active = False
        self.pending_diarization: list[tuple[object, float, float, bool]] = []
        self.candidate_speaker = ""
        self.live_assistance: list[tuple[str, dict[str, object]]] = []
        self.analysis_active = False
        self.analysis_pending_chunks: list[dict[str, object]] = []
        self.analysis_last_chunk = 0
        self.live_session: dict[str, object] | None = None
        self.question_set_date = tk.StringVar(value=date.today().isoformat())
        self.daily_questions = self._read_daily_questions(date.today().isoformat())
        self.questions_asked: list[str] = []
        self.question_choice_map: dict[str, str] = {}
        self._style()
        self._build()
        self._refresh_list()
        self.after(0, self._refresh_local_models)
        self.after(150, self._poll_live_events)

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"), foreground=INK)
        style.configure("Sub.TLabel", foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=INK)
        style.configure("Metric.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 17, "bold"))
        style.configure("MetricName.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#244bd4")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 10), font=("Segoe UI", 10))

    @staticmethod
    def _question_file_for_date(day: str) -> Path:
        parsed = datetime.strptime(day, "%Y-%m-%d")
        if parsed.strftime("%Y-%m-%d") != day:
            raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.")
        return QUESTION_SETS / f"{day}.json"

    @classmethod
    def _read_daily_questions(cls, day: str) -> list[str]:
        try:
            payload = json.loads(cls._question_file_for_date(day).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return []
            questions = payload.get("questions", [])
            if not isinstance(questions, list):
                return []
            return [question.strip() for question in questions if isinstance(question, str) and question.strip()]
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def _open_question_date_picker(self) -> None:
        try:
            selected = datetime.strptime(self.question_set_date.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            selected = date.today()

        window = tk.Toplevel(self)
        window.title("면접 날짜 선택")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()
        month_state = {"year": selected.year, "month": selected.month}
        month_label = tk.StringVar()

        header = ttk.Frame(window, padding=(12, 12, 12, 6))
        header.pack(fill="x")
        ttk.Button(header, text="◀", width=3, command=lambda: change_month(-1)).pack(side="left")
        ttk.Label(header, textvariable=month_label, anchor="center", font=("Segoe UI", 11, "bold")).pack(side="left", expand=True, fill="x", padx=8)
        ttk.Button(header, text="▶", width=3, command=lambda: change_month(1)).pack(side="right")

        calendar_grid = ttk.Frame(window, padding=(12, 0, 12, 6))
        calendar_grid.pack()
        footer = ttk.Frame(window, padding=(12, 0, 12, 12))
        footer.pack(fill="x")

        def choose_day(day: int) -> None:
            current_day = self.question_set_date.get().strip()
            current_questions = self._parsed_question_lines()
            try:
                current_saved = self._read_daily_questions(current_day)
            except ValueError:
                current_saved = []
            if current_questions != current_saved:
                if not messagebox.askyesno(
                    "편집 내용 확인",
                    "아직 저장하지 않은 질문 내용이 있습니다. 선택한 날짜의 질문을 불러오면 현재 편집 내용이 바뀝니다. 계속할까요?",
                    parent=window,
                ):
                    return
            selected_day = date(month_state["year"], month_state["month"], day).isoformat()
            self.question_set_date.set(selected_day)
            window.destroy()
            self._load_daily_question_set()

        def render_month() -> None:
            for child in calendar_grid.winfo_children():
                child.destroy()
            year, month = month_state["year"], month_state["month"]
            month_label.set(f"{year}년 {month}월")
            for column, weekday in enumerate(("월", "화", "수", "목", "금", "토", "일")):
                ttk.Label(calendar_grid, text=weekday, anchor="center", width=5).grid(row=0, column=column, padx=1, pady=(4, 6))
            for row, week in enumerate(month_calendar.monthcalendar(year, month), start=1):
                for column, day in enumerate(week):
                    if day == 0:
                        ttk.Label(calendar_grid, text="", width=5).grid(row=row, column=column, padx=1, pady=1)
                    else:
                        ttk.Button(
                            calendar_grid, text=str(day), width=4,
                            command=lambda value=day: choose_day(value),
                        ).grid(row=row, column=column, padx=1, pady=1)

        def change_month(offset: int) -> None:
            moved = month_state["month"] + offset
            if moved < 1:
                month_state["year"] -= 1
                month_state["month"] = 12
            elif moved > 12:
                month_state["year"] += 1
                month_state["month"] = 1
            else:
                month_state["month"] = moved
            render_month()

        def choose_today() -> None:
            today = date.today()
            month_state.update(year=today.year, month=today.month)
            render_month()
            choose_day(today.day)

        ttk.Button(footer, text="오늘", command=choose_today).pack(side="left")
        ttk.Button(footer, text="닫기", command=window.destroy).pack(side="right")
        render_month()

    def _build_question_setup_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        ttk.Label(parent, text="당일 공통 질문 30개 준비", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="면접 전에 질문을 한 줄에 하나씩 입력하세요. 저장한 날짜의 질문 세트는 모든 참가자에게 공통으로 사용됩니다.",
            style="Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="면접 날짜").pack(side="left", padx=(0, 8))
        ttk.Entry(toolbar, textvariable=self.question_set_date, width=14).pack(side="left")
        ttk.Button(toolbar, text="달력에서 선택", command=self._open_question_date_picker).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="날짜 질문 불러오기", command=self._load_daily_question_set).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="30개 저장", command=self._save_daily_question_set, style="Accent.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="날짜 질문 삭제", command=self._delete_daily_question_set).pack(side="left", padx=(8, 0))
        self.question_count_label = ttk.Label(toolbar, text="0 / 30 질문", style="Sub.TLabel")
        self.question_count_label.pack(side="right")

        editor_panel = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        editor_panel.grid(row=3, column=0, sticky="nsew")
        editor_panel.rowconfigure(0, weight=1)
        editor_panel.columnconfigure(0, weight=1)
        self.question_editor = tk.Text(editor_panel, wrap="word", undo=True, bg=PANEL, fg=INK, relief="flat",
                                       borderwidth=0, padx=12, pady=12, font=("Segoe UI", 10), height=25)
        editor_scroll = ttk.Scrollbar(editor_panel, orient="vertical", command=self.question_editor.yview)
        self.question_editor.configure(yscrollcommand=editor_scroll.set)
        self.question_editor.grid(row=0, column=0, sticky="nsew")
        editor_scroll.grid(row=0, column=1, sticky="ns")
        self.question_editor.bind("<KeyRelease>", self._update_question_count)
        self._load_daily_question_set(show_message=False)

    def _parsed_question_lines(self) -> list[str]:
        text = self.question_editor.get("1.0", "end-1c")
        questions = []
        for line in text.splitlines():
            question = re.sub(r"^\s*(?:Q\s*)?\d{1,2}\s*[.)\-:：]\s*", "", line, flags=re.IGNORECASE).strip()
            if question:
                questions.append(question)
        return questions

    def _update_question_count(self, _event: object = None) -> None:
        if hasattr(self, "question_count_label"):
            self.question_count_label.configure(text=f"{len(self._parsed_question_lines())} / 30 질문")

    def _load_daily_question_set(self, show_message: bool = True) -> None:
        day = self.question_set_date.get().strip()
        try:
            path = self._question_file_for_date(day)
        except ValueError:
            if show_message:
                messagebox.showerror("날짜 형식", "날짜를 YYYY-MM-DD 형식으로 입력해 주세요.", parent=self)
            return
        if self._active_session_uses_question_date(day):
            messagebox.showinfo("면접 진행 중", "현재 녹음에 사용하는 당일 질문 세트는 녹음이 끝난 뒤에 불러올 수 있습니다.", parent=self)
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            questions = payload.get("questions", []) if isinstance(payload, dict) else []
            if not isinstance(questions, list):
                raise ValueError("질문 목록 형식이 올바르지 않습니다.")
        except FileNotFoundError:
            questions = []
        except (OSError, json.JSONDecodeError, ValueError) as error:
            if show_message:
                messagebox.showerror("질문 세트 읽기 실패", str(error), parent=self)
            return
        self.question_editor.delete("1.0", tk.END)
        self.question_editor.insert("1.0", "\n".join(str(question) for question in questions))
        self._update_question_count()
        if day == date.today().isoformat():
            self.daily_questions = [str(question).strip() for question in questions if str(question).strip()]
            self._refresh_live_question_choices()
        if show_message:
            self.status.configure(text=f"{day} 질문 {len(questions)}개를 불러왔습니다.")

    def _delete_daily_question_set(self) -> None:
        day = self.question_set_date.get().strip()
        try:
            path = self._question_file_for_date(day)
        except ValueError:
            messagebox.showerror("날짜 형식", "날짜는 YYYY-MM-DD 형식으로 입력해 주세요.", parent=self)
            return
        if self._active_session_uses_question_date(day):
            messagebox.showinfo("면접 진행 중", "현재 녹음에서 사용하는 질문 세트는 녹음이 끝난 뒤에 삭제할 수 있습니다.", parent=self)
            return
        if not messagebox.askyesno(
            "날짜 질문 삭제",
            f"{day}에 저장된 공통 질문 목록을 삭제하고 편집기 내용을 비울까요?\n면접 기록과 각 회차에 저장된 질문은 삭제되지 않습니다.",
            parent=self,
        ):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            messagebox.showerror("질문 목록 삭제 실패", str(error), parent=self)
            return
        self.question_editor.delete("1.0", tk.END)
        self._update_question_count()
        if day == date.today().isoformat():
            self.daily_questions = []
            self._refresh_live_question_choices()
        self.status.configure(text=f"{day} 공통 질문 목록을 삭제했습니다. 면접 기록은 보존했습니다.")

    def _save_daily_question_set(self) -> None:
        day = self.question_set_date.get().strip()
        try:
            path = self._question_file_for_date(day)
        except ValueError:
            messagebox.showerror("날짜 형식", "날짜를 YYYY-MM-DD 형식으로 입력해 주세요.", parent=self)
            return
        if self._active_session_uses_question_date(day):
            messagebox.showinfo("면접 진행 중", "현재 녹음에 사용하는 당일 질문 세트는 녹음이 끝난 뒤에 수정할 수 있습니다.", parent=self)
            return
        questions = self._parsed_question_lines()
        if len(questions) != 30:
            messagebox.showinfo("질문 개수 확인", f"질문을 한 줄에 하나씩 정확히 30개 입력해 주세요. 현재 {len(questions)}개입니다.", parent=self)
            return
        existing_questions = self._read_daily_questions(day)
        # 기록 삭제 등으로 화면을 연 뒤 저장 데이터가 바뀌었을 수 있으므로
        # 저장 직전에 디스크 기준으로 회차 목록을 다시 읽습니다.
        self.sessions = find_sessions()
        sessions_on_day = [session for session in self.sessions if str(session["date"])[:10] == day]
        if sessions_on_day and questions != existing_questions:
            participants = ", ".join(sorted({str(session["participant_name"]) for session in sessions_on_day}))
            if path.exists():
                messagebox.showinfo(
                    "공통 질문 고정",
                    f"{day} 날짜에 저장된 면접 기록이 있어 질문 세트를 바로 수정할 수 없습니다.\n기록 참가자: {participants}\n기존 질문 목록을 먼저 삭제해 주세요.",
                    parent=self,
                )
                return
            if not messagebox.askyesno(
                "과거 기록 보존",
                f"{day} 날짜에는 이미 면접 기록이 있습니다.\n기록 참가자: {participants}\n\n삭제된 공통 질문 세트를 새로 저장할까요? 기존 면접 기록에 저장된 질문과 평가는 변경되지 않습니다.",
                parent=self,
            ):
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": day, "questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8")
        if day == date.today().isoformat():
            self.daily_questions = questions
            self._refresh_live_question_choices()
        self.status.configure(text=f"{day} 공통 질문 30개를 저장했습니다.")

    def _active_session_uses_question_date(self, day: str) -> bool:
        return bool(
            self.live_session
            and self.live_session.get("question_set_date") == day
            and hasattr(self, "start_live_button")
            and str(self.start_live_button.cget("state")) == "disabled"
        )

    def _refresh_live_question_choices(self) -> None:
        if not hasattr(self, "live_question_selector"):
            return
        self.question_choice_map = {}
        for index, question in enumerate(self.daily_questions, start=1):
            self.question_choice_map[f"{index:02d}. {question}"] = question
        self.live_question_selector.configure(values=list(self.question_choice_map))
        self.live_question_choice.set("")
        self.live_question.set("")

    def _activate_question(self, choice: str) -> None:
        question = self.question_choice_map.get(choice, "")
        self.live_question.set(question)
        if not question:
            return
        if self.live_session:
            self.live_session["question"] = question
            asked = self.live_session.setdefault("questions_asked", [])
            if question not in asked:
                asked.append(question)
            self.questions_asked = list(asked)
            self._save_live_session()
        index = list(self.question_choice_map).index(choice) + 1
        self.live_state.configure(text=f"현재 질문 {index}/30 선택됨")

    def _on_question_selected(self, _event: object = None) -> None:
        self._activate_question(self.live_question_choice.get())

    def _random_question(self) -> None:
        choices = list(self.question_choice_map)
        if not choices:
            self.live_state.configure(text="먼저 질문 준비에서 오늘의 공통 질문 30개를 저장해 주세요.")
            return
        current = self.live_question_choice.get()
        candidates = [choice for choice in choices if choice != current] or choices
        choice = random.choice(candidates)
        self.live_question_choice.set(choice)
        self._activate_question(choice)

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(24, 20, 24, 10))
        header.pack(fill="x")
        ttk.Label(header, text="모의 면접 기록실", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="저장된 면접 기록을 찾아보고 전사·평가·음성을 한 곳에서 확인하세요.", style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        self.pages = ttk.Notebook(self)
        self.pages.pack(fill="both", expand=True, padx=16, pady=(6, 0))
        records_page = ttk.Frame(self.pages, padding=(4, 10, 4, 8))
        self.pages.add(records_page, text="저장 기록")
        body = ttk.Frame(records_page)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=280)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(left, text="면접 기록", style="Panel.TLabel", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._refresh_list())
        search_box = ttk.Entry(left, textvariable=self.search)
        search_box.pack(fill="x", pady=(0, 10))
        search_box.insert(0, "")
        tree_frame = ttk.Frame(left, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.session_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.session_tree.column("#0", width=240, minwidth=200, stretch=True)
        tree_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.session_tree.yview)
        self.session_tree.configure(yscrollcommand=tree_scrollbar.set)
        self.session_tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.pack(side="right", fill="y")
        self.session_tree.bind("<<TreeviewSelect>>", self._select_session)
        self.session_items: dict[str, dict[str, object]] = {}
        ttk.Button(left, text="기록 새로고침", command=self._reload).pack(fill="x", pady=(10, 0))
        self.delete_session_button = ttk.Button(left, text="선택 회차 삭제", command=self._delete_selected_session, state="disabled")
        self.delete_session_button.pack(fill="x", pady=(6, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.detail_header = ttk.Frame(right, style="Panel.TFrame", padding=16)
        self.detail_header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.detail_header.columnconfigure(0, weight=1)
        self.session_title = ttk.Label(self.detail_header, text="회차를 선택하세요", style="Panel.TLabel", font=("Segoe UI", 16, "bold"))
        self.session_title.grid(row=0, column=0, sticky="w")
        self.session_subtitle = ttk.Label(self.detail_header, text="", style="Sub.TLabel")
        self.session_subtitle.grid(row=1, column=0, sticky="w", pady=(4, 12))
        metrics = ttk.Frame(self.detail_header, style="Panel.TFrame")
        metrics.grid(row=2, column=0, sticky="ew")
        self.metric_labels: dict[str, ttk.Label] = {}
        for column, key in enumerate(("text_score", "voice_score", "total_score")):
            card = ttk.Frame(metrics, style="Panel.TFrame", padding=(0, 0, 28, 0))
            card.grid(row=0, column=column, sticky="w")
            self.metric_labels[key] = ttk.Label(card, text="—", style="Metric.TLabel")
            self.metric_labels[key].pack(anchor="w")
            label = {"text_score": "텍스트 평가", "voice_score": "음성 전달", "total_score": "최종 총점"}[key]
            ttk.Label(card, text=label, style="MetricName.TLabel").pack(anchor="w")
        self.audio_button = ttk.Button(self.detail_header, text="음성 파일 열기", command=self._open_audio, style="Accent.TButton")
        self.audio_button.grid(row=0, column=1, rowspan=2, sticky="e")

        self.notebook = ttk.Notebook(right)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.transcript_view = self._add_text_tab("대화 전사")
        self.evaluation_view = self._add_text_tab("평가 결과")
        self.recommendation_view = self._add_text_tab("추천 질문")
        self.assistance_view = self._add_text_tab("실시간 조언")

        question_page = ttk.Frame(self.pages, padding=18)
        self.question_setup_page = question_page
        self.pages.add(question_page, text="질문 준비")
        self._build_question_setup_page(question_page)

        live_page = ttk.Frame(self.pages, padding=18)
        self.pages.add(live_page, text="실시간 면접")
        self._build_live_page(live_page)

        self.status = ttk.Label(self, text="", style="Sub.TLabel", anchor="w", padding=(22, 0, 22, 10))
        self.status.pack(fill="x")

    def _refresh_local_models(self) -> None:
        try:
            models = list_local_models()
            self.local_model_selector.configure(values=models)
            selected = self.local_model.get().strip()
            if models and selected not in models:
                self.local_model.set(MODEL_NAME if MODEL_NAME in models else models[0])
            self.live_state.configure(
                text=f"Ollama 연결됨 · 설치된 로컬 모델 {len(models)}개" if models else "Ollama 연결됨 · 설치된 모델이 없습니다. 설치 안내를 확인해 주세요."
            )
        except RuntimeError as error:
            self.live_state.configure(text=str(error))

    def _build_live_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=0)
        ttk.Label(parent, text="실시간 면접 보조", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="음성을 PC에서 전사·화자 분리하고, 지정한 면접자 답변을 로컬 AI가 분석해 확인할 점과 꼬리질문을 제안합니다.",
            style="Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))

        settings = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        settings.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="면접자 이름", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        self.live_people = list_participants()
        self.live_participant = tk.StringVar(value="")
        self.live_participant_entry = ttk.Combobox(
            settings,
            textvariable=self.live_participant,
            values=sorted(self.live_people.values(), key=str.casefold),
            state="normal",
            width=22,
        )
        self.live_participant_entry.grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(settings, text="면접 주제", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.live_topic = tk.StringVar(value="게임 엔진 클라이언트 프로그래머")
        ttk.Entry(settings, textvariable=self.live_topic).grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(settings, text="현재 질문", style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        self.live_question = tk.StringVar()
        question_picker = ttk.Frame(settings, style="Panel.TFrame")
        question_picker.grid(row=2, column=1, sticky="ew", pady=5)
        question_picker.columnconfigure(0, weight=1)
        self.live_question_choice = tk.StringVar(value="")
        self.live_question_selector = ttk.Combobox(question_picker, textvariable=self.live_question_choice, state="readonly")
        self.live_question_selector.grid(row=0, column=0, sticky="ew")
        self.live_question_selector.bind("<<ComboboxSelected>>", self._on_question_selected)
        ttk.Button(question_picker, text="랜덤 질문", command=self._random_question).grid(row=0, column=1, padx=(8, 0))
        self._refresh_live_question_choices()
        self.live_question_display = ttk.Label(
            settings,
            textvariable=self.live_question,
            style="Sub.TLabel",
            justify="left",
            wraplength=900,
        )
        self.live_question_display.grid(row=3, column=1, sticky="ew", pady=(2, 8))

        ttk.Label(settings, text="로컬 분석 모델", style="Panel.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=(8, 4))
        model_row = ttk.Frame(settings, style="Panel.TFrame")
        model_row.grid(row=4, column=1, sticky="ew", pady=(8, 4))
        model_row.columnconfigure(0, weight=1)
        self.local_model = tk.StringVar(value=MODEL_NAME)
        self.local_model_selector = ttk.Combobox(model_row, textvariable=self.local_model, values=(), state="normal")
        self.local_model_selector.grid(row=0, column=0, sticky="ew")
        ttk.Button(model_row, text="설치된 모델 새로고침", command=self._refresh_local_models).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            settings,
            text="Ollama와 로컬 모델이 필요합니다. 녹음·전사·화자 분리·분석 내용은 외부로 전송되지 않습니다.",
            style="Sub.TLabel",
        ).grid(row=5, column=1, sticky="w", pady=(0, 4))

        ttk.Label(settings, text="면접자 화자", style="Panel.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=(8, 4))
        speaker_row = ttk.Frame(settings, style="Panel.TFrame")
        speaker_row.grid(row=6, column=1, sticky="ew", pady=(8, 4))
        speaker_row.columnconfigure(0, weight=1)
        self.speaker_choice = tk.StringVar(value="")
        self.speaker_selector = ttk.Combobox(speaker_row, textvariable=self.speaker_choice, values=(), state="readonly", width=20)
        self.speaker_selector.grid(row=0, column=0, sticky="w")
        self.speaker_selector.bind("<<ComboboxSelected>>", self._candidate_speaker_changed)
        self.speaker_state = ttk.Label(speaker_row, text="화자 분석 결과를 기다리는 중", style="Sub.TLabel")
        self.speaker_state.grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(settings, text="예상 화자 수", style="Panel.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=5)
        speaker_count_row = ttk.Frame(settings, style="Panel.TFrame")
        speaker_count_row.grid(row=7, column=1, sticky="ew", pady=5)
        self.expected_speaker_count = tk.StringVar(value="3")
        self.expected_speaker_selector = ttk.Combobox(
            speaker_count_row, textvariable=self.expected_speaker_count,
            values=tuple(str(count) for count in range(1, 7)), state="readonly", width=6,
        )
        self.expected_speaker_selector.pack(side="left")
        ttk.Label(speaker_count_row, text="실제 인원수에 맞추면 한 사람을 여러 화자로 나누는 오류를 줄입니다.", style="Sub.TLabel").pack(side="left", padx=(8, 0))
        ttk.Label(settings, text="질문을 선택하면 분석이 더 정확해집니다. 면접자 화자를 지정하면 해당 발화만 분석하고, 지정 전에도 최근 대화로 분석을 시작합니다.", style="Sub.TLabel").grid(row=8, column=1, sticky="w", pady=(0, 4))

        controls = ttk.Frame(parent)
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.start_live_button = ttk.Button(controls, text="●  녹음 시작", command=self._start_live, style="Accent.TButton")
        self.start_live_button.pack(side="left")
        self.stop_live_button = ttk.Button(controls, text="■  녹음 종료 및 저장", command=self._stop_live, state="disabled")
        self.stop_live_button.pack(side="left", padx=(8, 8))
        self.analyze_live_button = ttk.Button(controls, text="최근 답변 분석", command=lambda: self._request_live_analysis(manual=True))
        self.analyze_live_button.pack(side="left")
        self.live_state = ttk.Label(controls, text="대기 중", style="Sub.TLabel")
        self.live_state.pack(side="left", padx=14)

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.grid(row=4, column=0, sticky="nsew")
        parent.rowconfigure(4, weight=1)
        transcript_panel = ttk.Frame(panes, style="Panel.TFrame", padding=10)
        advice_panel = ttk.Frame(panes, style="Panel.TFrame", padding=10)
        panes.add(transcript_panel, weight=2)
        panes.add(advice_panel, weight=3)
        ttk.Label(transcript_panel, text="실시간 전사", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(2, 8))
        transcript_tabs = ttk.Notebook(transcript_panel)
        transcript_tabs.pack(fill="both", expand=True)
        all_transcript_tab = ttk.Frame(transcript_tabs, style="Panel.TFrame", padding=4)
        candidate_transcript_tab = ttk.Frame(transcript_tabs, style="Panel.TFrame", padding=4)
        transcript_tabs.add(all_transcript_tab, text="전체 대화")
        transcript_tabs.add(candidate_transcript_tab, text="면접자 답변만")
        self.live_transcript_view = tk.Text(all_transcript_tab, wrap="word", bg=PANEL, fg=INK, relief="flat", borderwidth=0,
                                            padx=10, pady=10, font=("Segoe UI", 10), state="disabled")
        self.live_transcript_view.pack(fill="both", expand=True)
        self.live_candidate_view = tk.Text(candidate_transcript_tab, wrap="word", bg=PANEL, fg=INK, relief="flat", borderwidth=0,
                                           padx=10, pady=10, font=("Segoe UI", 10), state="disabled")
        self.live_candidate_view.pack(fill="both", expand=True)
        ttk.Label(advice_panel, text="AI 면접관 메모", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(2, 2))
        self.live_ai_state = ttk.Label(advice_panel, text="대기 중 · 녹음 시작 후 AI 상태가 여기에 표시됩니다", style="Sub.TLabel", wraplength=600)
        self.live_ai_state.pack(anchor="w", fill="x", pady=(0, 8))
        self.live_advice_view = tk.Text(advice_panel, wrap="word", bg=PANEL, fg=INK, relief="flat", borderwidth=0,
                                        padx=12, pady=12, font=("Segoe UI", 11), state="disabled")
        advice_scrollbar = ttk.Scrollbar(advice_panel, orient="vertical", command=self.live_advice_view.yview)
        self.live_advice_view.configure(yscrollcommand=advice_scrollbar.set)
        self.live_advice_view.pack(side="left", fill="both", expand=True)
        advice_scrollbar.pack(side="right", fill="y")
        ttk.Label(parent, text="화자 분리는 추정이라 오분류될 수 있습니다. 면접자 화자를 지정하면 해당 발화를 중심으로, 지정 전에는 최근 대화 문맥으로 Ollama가 답변 메모와 꼬리질문을 생성합니다.", style="Sub.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 0))

    def _resolve_participant_id(self, name: str) -> str:
        normalized = name.casefold()
        matches = [participant_id for participant_id, saved_name in self.live_people.items()
                   if " ".join(saved_name.split()).casefold() == normalized]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            messagebox.showerror("이름 중복", "같은 이름의 참가자가 여러 명 있습니다. 참가자 기록에서 이름을 구분해 주세요.", parent=self)
            return ""
        messagebox.showerror("이름 확인", "저장된 참가자 이름을 찾지 못했습니다. 목록에서 이름을 선택하거나 철자를 확인해 주세요.", parent=self)
        return ""

    def _start_live(self) -> None:
        if str(self.start_live_button.cget("state")) == "disabled":
            return
        if self.diarization_active or self.pending_diarization:
            self.live_state.configure(text="이전 회차의 화자 분석이 끝난 뒤 새 회차를 시작해 주세요.")
            return
        if self.analysis_active:
            self.live_state.configure(text="현재 로컬 AI 분석이 끝난 뒤 새 회차를 시작해 주세요.")
            return
        entered_name = " ".join(self.live_participant.get().split())
        if not entered_name:
            messagebox.showinfo("이름 입력", "면접자 이름을 입력해 주세요.", parent=self)
            return
        participant_id = self._resolve_participant_id(entered_name)
        if not participant_id:
            return
        today = date.today().isoformat()
        self.daily_questions = self._read_daily_questions(today)
        self._refresh_live_question_choices()
        if len(self.daily_questions) != 30:
            self.question_set_date.set(today)
            self._load_daily_question_set(show_message=False)
            self.pages.select(self.question_setup_page)
            messagebox.showinfo("공통 질문 준비", "오늘 면접에서 사용할 공통 질문 30개를 먼저 저장해 주세요.", parent=self)
            return
        participant_dir = RECORDS / participant_id
        sessions_dir = participant_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sequence = 1
        while (sessions_dir / f"{participant_id}-{today}-{sequence:02d}").exists():
            sequence += 1
        session_id = f"{participant_id}-{today}-{sequence:02d}"
        session_dir = sessions_dir / session_id
        session_dir.mkdir()
        audio_path = session_dir / f"{session_id}.wav"
        last_round = max(
            (int(item["round_number"]) for item in self.sessions if item["participant_id"] == participant_id),
            default=0,
        )
        round_label = f"{last_round + 1}회차"
        self.live_session = {
            "participant_id": participant_id,
            "participant_name": self.live_people.get(participant_id, entered_name),
            "session_id": session_id,
            "date": today,
            "round_label": round_label,
            "topic": self.live_topic.get().strip(),
            "expected_speakers": int(self.expected_speaker_count.get()),
            "question": "",
            "question_set_date": today,
            "question_set": list(self.daily_questions),
            "questions_asked": [],
            "folder": session_dir,
            "audio_path": audio_path,
        }
        self.live_chunks = []
        self.diarization_turns = []
        self.diarization_active = False
        self.pending_diarization = []
        reset_diarization()
        self.candidate_speaker = ""
        self.speaker_choice.set("")
        self.speaker_selector.configure(values=())
        self.speaker_state.configure(text="화자 분석 결과를 기다리는 중")
        self.live_assistance = []
        self.analysis_pending_chunks = []
        self.analysis_last_chunk = 0
        self.questions_asked = []
        self.live_question_choice.set("")
        self.live_question.set("")
        self._render_live_transcript()
        self.live_state.configure(text="마이크 연결과 로컬 음성 인식 모델을 준비하고 있습니다…")
        self._put_text(self.live_advice_view, "전사 내용이 쌓이면 로컬 AI가 확인할 주장과 꼬리질문을 제안합니다.")
        self.live_ai_state.configure(text="전사 대기 중 · Ollama 로컬 모델로 분석합니다")
        self.live_state.configure(text="준비 중")
        self.start_live_button.configure(state="disabled")
        self.stop_live_button.configure(state="normal")
        asr_prompt = (
            f"한국어 게임 클라이언트 개발 면접. 지원자 이름은 {self.live_session['participant_name']}. "
            "기술 용어: 게임 클라이언트, 게임 엔진, C++, C#, Unity, Unreal Engine, 렌더링, 셰이더, "
            "프레임, 최적화, 메모리 관리, 자료구조, 알고리즘, 네트워크, 멀티스레드, 객체지향, 디버깅, 포트폴리오."
        )
        self.live_capture = LiveAudioCapture(audio_path, model_size="small", initial_prompt=asr_prompt)
        self.live_capture.start()

    def _stop_live(self) -> None:
        if self.live_capture:
            self.live_capture.stop()
            self.stop_live_button.configure(state="disabled")
            self.live_state.configure(text="녹음을 마무리하고 마지막 구간을 전사합니다…")

    def _poll_live_events(self) -> None:
        capture = self.live_capture
        if capture:
            while True:
                try:
                    event = capture.events.get_nowait()
                except queue.Empty:
                    break
                kind = event.get("type")
                if kind == "status":
                    self.live_state.configure(text=str(event.get("message", "처리 중")))
                elif kind == "started":
                    self.live_state.configure(text=str(event.get("message", "녹음 중")))
                elif kind == "transcript":
                    self._receive_live_transcript(
                        float(event.get("start_seconds", 0)),
                        float(event.get("end_seconds", event.get("start_seconds", 0))),
                        str(event.get("text", "")),
                    )
                elif kind == "diarization_snapshot":
                    self._request_speaker_diarization(
                        event.get("audio"),
                        float(event.get("start_seconds", 0)),
                        float(event.get("end_seconds", 0)),
                        bool(event.get("final", False)),
                    )
                elif kind == "diarization_result":
                    self.diarization_active = False
                    self.diarization_turns.extend(event.get("turns", []))
                    self.diarization_turns.sort(key=lambda turn: (float(turn["start"]), float(turn["end"])))
                    self._apply_speaker_turns()
                    speaker_count = len(self.speaker_selector.cget("values"))
                    self.speaker_state.configure(text=f"로컬 화자 분석 완료 · {speaker_count}명 감지")
                    if not self.candidate_speaker:
                        self.live_state.configure(text=f"화자 {speaker_count}명 감지 · 면접자 화자를 지정하면 후보자 발화만 분석합니다. 미지정 상태도 최근 대화 분석을 계속합니다.")
                    self._save_live_session()
                    self._request_live_analysis(manual=False)
                    if self.pending_diarization:
                        self._request_speaker_diarization(*self.pending_diarization.pop(0))
                elif kind == "diarization_error":
                    self.diarization_active = False
                    error_message = str(event.get("message", "화자 분석에 실패했습니다."))
                    self.speaker_state.configure(text=error_message)
                    self.live_state.configure(text=f"화자 분리 실패 · AI 후보자 분석은 시작되지 않았습니다: {error_message}")
                    self.pending_diarization.clear()
                elif kind == "ai_result":
                    self.analysis_active = False
                    result = event.get("result", {})
                    excerpt = str(event.get("excerpt", ""))
                    for chunk in self.analysis_pending_chunks:
                        chunk["analyzed"] = True
                    self.analysis_pending_chunks = []
                    if isinstance(result, dict):
                        self.live_assistance.append((excerpt, result))
                        self._render_live_assistance()
                        self._save_live_assistance()
                    self.live_state.configure(text="AI 참고 의견을 받았습니다 · 최종 판단은 면접관이 확인하세요")
                    self.live_ai_state.configure(text="분석 완료 · 최신 의견을 패널 맨 위에 표시했습니다")
                    self.analyze_live_button.configure(state="normal")
                    self._request_live_analysis(manual=False)
                elif kind == "ai_error":
                    self.analysis_active = False
                    for chunk in self.analysis_pending_chunks:
                        chunk["analyzed"] = False
                    self.analysis_pending_chunks = []
                    error_message = str(event.get("message", "AI 분석에 실패했습니다."))
                    self.live_state.configure(text=error_message)
                    self.live_ai_state.configure(text=f"AI 분석 실패 · {error_message}")
                    self.analyze_live_button.configure(state="normal")
                elif kind == "error":
                    self.live_state.configure(text=str(event.get("message", "실시간 처리 오류")))
                    self._save_live_session()
                    self.start_live_button.configure(state="normal")
                    self.stop_live_button.configure(state="disabled")
                    self.analyze_live_button.configure(state="normal")
                elif kind == "finished":
                    self.live_state.configure(text="완료 · 녹음과 전사를 회차 기록에 저장했습니다.")
                    self._save_live_session()
                    self.start_live_button.configure(state="normal")
                    self.stop_live_button.configure(state="disabled")
                    self.analyze_live_button.configure(state="normal")
                    self.sessions = find_sessions()
                    self._refresh_list()
        self.after(150, self._poll_live_events)

    def _receive_live_transcript(self, start_seconds: float, end_seconds: float, text: str) -> None:
        if not text.strip():
            return
        self.live_chunks.append({"start": start_seconds, "end": max(start_seconds, end_seconds), "text": text.strip(), "speaker": "", "analyzed": False})
        self.live_state.configure(text=f"녹음 중 · {len(self.live_chunks)}개 전사 구간")
        self._apply_speaker_turns()
        self._render_live_transcript()
        self._save_live_session()
        self._request_live_analysis(manual=False)

    def _render_live_transcript(self) -> None:
        rows = []
        for chunk in self.live_chunks:
            start = float(chunk["start"])
            minutes, seconds = divmod(int(start), 60)
            speaker = str(chunk.get("speaker") or "화자 확인 중")
            rows.append(f"[{minutes:02d}:{seconds:02d}]  [{speaker}]  {chunk['text']}")
        self._put_text(self.live_transcript_view, "\n\n".join(rows) or "아직 전사된 음성이 없습니다.")
        self.live_transcript_view.yview_moveto(1.0)
        candidate_rows = [
            f"[{int(float(chunk['start']) // 60):02d}:{int(float(chunk['start']) % 60):02d}]  {chunk['text']}"
            for chunk in self.live_chunks
            if self.candidate_speaker and chunk.get("speaker") == self.candidate_speaker
        ]
        candidate_message = (
            "면접자 화자를 선택하면 해당 발화만 여기에 표시됩니다."
            if not self.candidate_speaker
            else "지정한 면접자로 분류된 발화가 아직 없습니다."
        )
        self._put_text(self.live_candidate_view, "\n\n".join(candidate_rows) or candidate_message)
        self.live_candidate_view.yview_moveto(1.0)

    def _request_speaker_diarization(self, audio: object, start_seconds: float, end_seconds: float, final: bool) -> None:
        if audio is None:
            return
        if self.diarization_active:
            self.pending_diarization.append((audio, start_seconds, end_seconds, final))
            return
        self.diarization_active = True
        self.speaker_state.configure(text=f"{int(end_seconds)}초까지 화자 구분 중…")
        expected_speakers = int(self.live_session.get("expected_speakers", 2)) if self.live_session else 2
        event_queue = self.live_capture.events if self.live_capture else None
        def run_diarization() -> None:
            try:
                turns = diarize_audio_samples(audio, 16_000, start_seconds, max_speakers=expected_speakers)
                if event_queue:
                    event_queue.put({"type": "diarization_result", "turns": turns, "end_seconds": end_seconds, "final": final})
            except Exception as error:
                if event_queue:
                    event_queue.put({"type": "diarization_error", "message": str(error), "final": final})

        threading.Thread(target=run_diarization, name="interview-speaker-diarization", daemon=True).start()

    def _apply_speaker_turns(self) -> None:
        labels = sorted({str(turn["speaker"]) for turn in self.diarization_turns if turn.get("speaker")})
        self.speaker_selector.configure(values=labels)
        if self.candidate_speaker not in labels:
            self.candidate_speaker = ""
            self.speaker_choice.set("")
        for chunk in self.live_chunks:
            start, end = float(chunk["start"]), float(chunk["end"])
            duration = max(0.25, end - start)
            overlap_by_speaker: dict[str, float] = {}
            for turn in self.diarization_turns:
                overlap = max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"])))
                if overlap:
                    speaker = str(turn["speaker"])
                    overlap_by_speaker[speaker] = overlap_by_speaker.get(speaker, 0.0) + overlap
            best = max(overlap_by_speaker, key=overlap_by_speaker.get) if overlap_by_speaker else ""
            chunk["speaker"] = best if best and overlap_by_speaker[best] / duration >= 0.25 else ""
        self._render_live_transcript()
        if self.live_session:
            self.live_session["candidate_speaker"] = self.candidate_speaker

    def _candidate_speaker_changed(self, _event: object = None) -> None:
        self.candidate_speaker = self.speaker_choice.get().strip()
        if self.live_session:
            self.live_session["candidate_speaker"] = self.candidate_speaker
        self._apply_speaker_turns()
        if self.candidate_speaker:
            self.speaker_state.configure(text=f"{self.candidate_speaker}를 면접자로 지정했습니다")
            self.live_state.configure(text="면접자를 지정했습니다 · 답변 전사가 모이면 AI 메모가 자동으로 생성됩니다.")
            self._request_live_analysis(manual=False)
            self._save_live_session()
        else:
            self.live_state.configure(text="면접자 화자를 지정하지 않아도 최근 대화 분석을 진행합니다.")

    def _request_live_analysis(self, manual: bool) -> None:
        if not self.live_chunks:
            if manual:
                self.live_state.configure(text="먼저 답변 전사를 받아야 분석할 수 있습니다.")
            return
        if self.analysis_active:
            return
        model = self.local_model.get().strip()
        if not model:
            message = "로컬 분석 모델이 없습니다. Ollama에서 로컬 모델을 내려받은 뒤 ‘설치된 모델 새로고침’을 눌러 주세요."
            self.live_state.configure(text=message)
            self.live_ai_state.configure(text=f"AI 분석 대기: {message}")
            return
        if self.candidate_speaker:
            candidate_chunks = [chunk for chunk in self.live_chunks if chunk.get("speaker") == self.candidate_speaker]
            selected = candidate_chunks[-3:] if manual else [chunk for chunk in candidate_chunks if not chunk.get("analyzed")]
        else:
            # 화자 선택/분리가 지연되더라도 면접 보조가 멈추지 않도록 최근 대화를 분석합니다.
            # 로컬 모델에게 면접관 발화와 후보자 답변을 문맥으로 구분하도록 지시합니다.
            selected = self.live_chunks[-4:] if manual else [chunk for chunk in self.live_chunks if not chunk.get("analyzed")][-4:]
        if not selected:
            if manual:
                self.live_state.configure(text="분석할 면접자 전사를 기다리는 중입니다.")
            return
        excerpt = "\n".join(
            f"[{int(float(chunk['start']) // 60):02d}:{int(float(chunk['start']) % 60):02d}] [{chunk.get('speaker') or '화자 미확정'}] {chunk['text']}"
            for chunk in selected
        )
        transcript_length = sum(len(str(chunk.get("text", ""))) for chunk in selected)
        if transcript_length < 24:
            self.live_ai_state.configure(text=f"전사 수집 중 · {transcript_length}/24자")
            if manual:
                self.live_state.configure(text="분석할 전사 내용이 조금 더 필요합니다.")
            else:
                self.live_state.configure(text="전사 문장이 24자 이상 쌓이면 AI 분석을 시작합니다.")
            return
        self.analysis_pending_chunks = list(selected)
        for chunk in self.analysis_pending_chunks:
            chunk["analyzed"] = True
        self.analysis_active = True
        self.analyze_live_button.configure(state="disabled")
        self.live_state.configure(text=f"{model} 로컬 모델이 답변을 분석 중입니다…")
        self.live_ai_state.configure(text=f"분석 중 · {model} · 로컬 모델 응답을 기다리는 중")
        question = self.live_question.get().strip()
        if self.live_session:
            self.live_session["question"] = question
        event_queue = self.live_capture.events if self.live_capture else None

        def run_analysis() -> None:
            try:
                result = analyze_answer(question, excerpt, model)
                result["_question"] = question
                if event_queue:
                    event_queue.put({"type": "ai_result", "result": result, "excerpt": excerpt})
                else:
                    self.after(0, lambda: self._accept_standalone_analysis(excerpt, result))
            except Exception as error:
                message = str(error)
                if event_queue:
                    event_queue.put({"type": "ai_error", "message": message})
                else:
                    self.after(0, lambda m=message: self._standalone_analysis_error(m))

        threading.Thread(target=run_analysis, name="local-answer-analysis", daemon=True).start()

    def _accept_standalone_analysis(self, excerpt: str, result: dict[str, object]) -> None:
        self.analysis_active = False
        for chunk in self.analysis_pending_chunks:
            chunk["analyzed"] = True
        self.analysis_pending_chunks = []
        self.live_assistance.append((excerpt, result))
        self._render_live_assistance()
        self._save_live_assistance()
        self.live_state.configure(text="AI 참고 의견을 받았습니다 · 최종 판단은 면접관이 확인하세요")
        self.live_ai_state.configure(text="분석 완료 · 최신 의견을 패널 맨 위에 표시했습니다")
        self.analyze_live_button.configure(state="normal")

    def _standalone_analysis_error(self, message: str) -> None:
        self.analysis_active = False
        for chunk in self.analysis_pending_chunks:
            chunk["analyzed"] = False
        self.analysis_pending_chunks = []
        self.live_state.configure(text=message)
        self.live_ai_state.configure(text=f"AI 분석 실패 · {message}")
        self.analyze_live_button.configure(state="normal")

    def _render_live_assistance(self) -> None:
        view = self.live_advice_view
        view.configure(state="normal")
        view.delete("1.0", tk.END)
        view.tag_configure("analysis_title", foreground=ACCENT, font=("Segoe UI", 11, "bold"), spacing1=10, spacing3=5)
        view.tag_configure("section_title", foreground=INK, font=("Segoe UI", 10, "bold"), spacing1=7, spacing3=2)
        view.tag_configure("keyword", foreground="#174ea6", background="#eaf2ff", font=("Segoe UI", 11, "bold"), lmargin1=4, lmargin2=4, spacing1=3, spacing3=7)
        view.tag_configure("followup", foreground="#147a45", background="#eef9f1", lmargin1=8, lmargin2=8, spacing1=2, spacing3=3)
        view.tag_configure("muted", foreground=MUTED)

        if not self.live_assistance:
            view.insert(tk.END, "아직 AI 의견이 없습니다. 전사 내용이 모이면 요약과 꼬리질문이 표시됩니다.")
        for index, (excerpt, result) in reversed(list(enumerate(self.live_assistance, start=1))):
            times = re.findall(r"\[(\d{2}:\d{2})\]", excerpt)
            time_label = f"{times[0]}–{times[-1]}" if times else "시간 미확인"
            question = str(result.get("_question", "")).strip() or "대화에서 질문 문맥 추정"
            view.insert(tk.END, f"분석 {index}  ·  {time_label}  ·  신뢰도 {result.get('confidence', '미표기')}\n", "analysis_title")
            view.insert(tk.END, f"질문: {question}\n", "muted")
            keywords = result.get("keywords", [])
            if isinstance(keywords, list):
                keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
            else:
                keywords = []
            view.insert(tk.END, "키워드  ", "section_title")
            view.insert(tk.END, "  ·  ".join(keywords) if keywords else "추출된 키워드 없음", "keyword" if keywords else "muted")
            view.insert(tk.END, "\n")
            view.insert(tk.END, "핵심 요약\n", "section_title")
            view.insert(tk.END, f"{result.get('answer_summary', '요약 없음')}\n")

            view.insert(tk.END, "답변에서 확인할 주장\n", "section_title")
            claims = result.get("claims", [])
            if isinstance(claims, list) and claims:
                for claim in claims:
                    if isinstance(claim, dict):
                        assessment = str(claim.get("assessment", "검토 필요"))
                        statement = str(claim.get("claim", ""))
                        reason = str(claim.get("reason", ""))
                        view.insert(tk.END, f"• {assessment}  |  {statement}\n")
                        if reason:
                            view.insert(tk.END, f"  근거: {reason}\n", "muted")
            else:
                view.insert(tk.END, "• 뚜렷한 주장을 찾지 못했습니다. 전사 문장을 확인해 주세요.\n", "muted")

            view.insert(tk.END, "바로 이어서 물어볼 꼬리질문\n", "section_title")
            followups = result.get("follow_up_questions", [])
            if isinstance(followups, list) and followups:
                for question_index, followup in enumerate(followups, start=1):
                    if isinstance(followup, str) and followup.strip():
                        view.insert(tk.END, f"{question_index}. {followup.strip()}\n", "followup")
            else:
                view.insert(tk.END, "추천 질문이 없습니다.\n", "muted")

            checks = result.get("what_to_verify", [])
            if isinstance(checks, list) and checks:
                view.insert(tk.END, "추가로 확인할 점\n", "section_title")
                for item in checks:
                    if isinstance(item, str) and item.strip():
                        view.insert(tk.END, f"• {item.strip()}\n")
            view.insert(tk.END, "\n────────────────────────\n", "muted")

        view.configure(state="disabled")
        view.yview_moveto(0.0)

    def _save_live_session(self) -> None:
        session = self.live_session
        if not session:
            return
        folder = session["folder"]
        transcript = [
            "# 면접 대화 기록",
            "",
            f"- 참가자 ID: {session['participant_id']}",
            f"- 표시 이름: {session['participant_name']}",
            f"- 세션 ID: {session['session_id']}",
            f"- 기록일: {session['date']}",
            f"- 면접 회차: {session['round_label']}",
            f"- 공통 질문 세트 날짜: {session.get('question_set_date', session['date'])}",
            f"- 포지션 및 레벨: {session['topic']} / 미제공",
            "- 면접 유형: 실시간 모의 면접",
            f"- 음성 원본: [{session['audio_path'].name}](./{session['audio_path'].name})",
            "- 전사 방식: 로컬 faster-whisper 실시간 구간 전사",
            f"- 화자 구분: 로컬 SpeechBrain 임베딩 기반 구간 분석, 면접자 화자: {session.get('candidate_speaker') or '미지정'}",
            "",
            "## 자동 전사 (원문 검수 전)",
            "",
        ]
        transcript.extend(["## 실제 진행한 질문", ""])
        asked_questions = list(session.get("questions_asked", []))
        if asked_questions:
            question_set = list(session.get("question_set", []))
            for question in asked_questions:
                question_number = question_set.index(question) + 1 if question in question_set else "?"
                transcript.append(f"{question_number}. {question}")
        else:
            transcript.append("아직 선택된 질문이 없습니다.")
        transcript.append("")
        if self.live_chunks:
            for chunk in self.live_chunks:
                start = float(chunk["start"])
                minutes, seconds = divmod(int(start), 60)
                transcript.append(f"[자동 전사 · {minutes:02d}:{seconds:02d} · {chunk.get('speaker') or '미확인 화자'}] {chunk['text']}")
                transcript.append("")
        else:
            transcript.extend(["전사 구간이 없습니다. 음성 파일이 있으면 직접 확인해 주세요.", ""])
        (folder / "transcript.md").write_text("\n".join(transcript), encoding="utf-8")
        (folder / "speaker-turns.json").write_text(
            json.dumps(self.diarization_turns, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (folder / "question-set.json").write_text(
            json.dumps(
                {"date": session.get("question_set_date", session["date"]), "questions": session.get("question_set", [])},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        candidate_text = [
            "# 면접자 답변 추출",
            "",
            f"- 지정 화자: {session.get('candidate_speaker') or '미지정'}",
            "- 추출 방식: 전사 구간과 로컬 화자 분리 타임스탬프의 겹침 기준",
            "- 검수 안내: ASR 및 화자 분리 오류 가능성이 있으므로 제출 전 음성과 대조하세요.",
            "",
        ]
        selected_chunks = [chunk for chunk in self.live_chunks if session.get("candidate_speaker") and chunk.get("speaker") == session.get("candidate_speaker")]
        if selected_chunks:
            for chunk in selected_chunks:
                start = float(chunk["start"])
                candidate_text.append(f"[{int(start // 60):02d}:{int(start % 60):02d}] {chunk['text']}")
                candidate_text.append("")
        else:
            candidate_text.append("면접자 화자가 지정되지 않았거나, 지정 화자로 분류된 발화가 없습니다.")
        (folder / "candidate-answers.md").write_text("\n".join(candidate_text), encoding="utf-8")
        evaluation = f"""# 면접 평가 기록

- 참가자 ID: {session['participant_id']}
- 표시 이름: {session['participant_name']}
- 세션 ID: {session['session_id']}
- 평가 상태: 최종 평가 보류
- 루브릭 버전: text-2.0+voice-2.0 (수동 검토 필요)

## 평가 안내

이 회차는 실시간 보조 기능으로 기록되었습니다. 전사와 화자 분리는 자동 추정이며 사람이 확인하지 않았으므로, 실시간 AI 의견을 질문별 점수나 최종 점수로 간주하지 않습니다. 음성 점수도 후보자 구간을 확인한 뒤에만 평가합니다. 면접자 발화 추출은 `candidate-answers.md`를 확인하세요.

질문별 최종 평가와 점수는 전사·화자를 검수한 뒤 별도로 작성하세요. 생성된 실시간 AI 의견 또는 미실행 사유는 `live-assistance.md`에 보존됩니다.
"""
        (folder / "evaluation.md").write_text(evaluation, encoding="utf-8")
        self._save_live_assistance()

    def _save_live_assistance(self) -> None:
        if not self.live_session:
            return
        session = self.live_session
        sections = [
            "# 실시간 면접 보조 메모",
            "",
            f"- 세션 ID: {session['session_id']}",
            f"- 모델: {self.local_model.get().strip() or MODEL_NAME}",
            "- 성격: 잠정 참고 의견. 질문별 점수나 최종 평가가 아님.",
            f"- 화자: 로컬 SpeechBrain 임베딩 기반 분리, 지정 면접자: {session.get('candidate_speaker') or '미지정'}.",
            "",
        ]
        if not self.live_assistance:
            sections.extend(["로컬 분석 결과가 아직 없습니다. Ollama 실행 여부와 모델 설치를 확인하세요.", ""])
        for index, (excerpt, result) in enumerate(self.live_assistance, start=1):
            times = re.findall(r"\[(\d{2}:\d{2})\]", excerpt)
            time_label = f"{times[0]}–{times[-1]}" if times else "시간 미확인"
            sections.extend([
                f"## 분석 {index} · {time_label}", "",
                f"질문 맥락: {result.get('_question') or '대화에서 자동 추정'}", "",
                f"신뢰도 참고: {result.get('confidence', '미표기')}", "",
                f"요약: {result.get('answer_summary', '')}", "", "### 확인할 주장", "",
            ])
            claims = result.get("claims", [])
            if isinstance(claims, list):
                for claim in claims:
                    if isinstance(claim, dict):
                        sections.append(f"- **{claim.get('assessment', '검토 필요')}** — {claim.get('claim', '')}: {claim.get('reason', '')}")
            sections.extend(["", "### 추천 꼬리질문", ""])
            sections.extend(f"- {question}" for question in result.get("follow_up_questions", []) if isinstance(question, str))
            sections.extend(["", "### 추가 확인", ""])
            sections.extend(f"- {item}" for item in result.get("what_to_verify", []) if isinstance(item, str))
            sections.extend(["", "### 전사 근거", "", excerpt, ""])
        (session["folder"] / "live-assistance.md").write_text("\n".join(sections), encoding="utf-8")

    def _add_text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=title)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        view = tk.Text(frame, wrap="word", bg=PANEL, fg=INK, relief="flat", borderwidth=0,
                       padx=18, pady=16, font=("Segoe UI", 10), spacing1=2, spacing3=5,
                       state="disabled")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=view.yview)
        view.configure(yscrollcommand=scrollbar.set)
        view.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return view

    def _refresh_list(self) -> None:
        query = self.search.get().strip().casefold()
        selected_session_id = None
        for item_id in self.session_tree.selection():
            if item_id in self.session_items:
                selected_session_id = str(self.session_items[item_id]["session_id"])

        self.session_tree.delete(*self.session_tree.get_children())
        self.session_items = {}
        self.delete_session_button.configure(state="disabled")
        grouped: dict[str, list[dict[str, object]]] = {}
        for session in self.sessions:
            searchable = " ".join(str(session[key]) for key in ("participant_id", "participant_name", "session_id", "date", "round_label"))
            if query and query not in searchable.casefold():
                continue
            grouped.setdefault(str(session["participant_id"]), []).append(session)

        self.visible_sessions = []
        first_session_item = None
        for participant_id, person_sessions in sorted(
            grouped.items(), key=lambda item: str(item[1][0]["participant_name"]).casefold()
        ):
            name = str(person_sessions[0]["participant_name"])
            person_item = f"person-{participant_id}"
            self.session_tree.insert("", "end", iid=person_item, text=name, open=True)
            for session in sorted(person_sessions, key=lambda item: (int(item["round_number"]), item["sort_date"])):
                session_item = f"session-{session['session_id']}"
                label = f"{session['round_label']}    ·    {session['date']}"
                self.session_tree.insert(person_item, "end", iid=session_item, text=label)
                self.session_items[session_item] = session
                self.visible_sessions.append(session)
                if first_session_item is None:
                    first_session_item = session_item

        self.status.configure(text=f"{len(self.visible_sessions)}개 회차 · 이름별로 묶어 표시")
        selected_item = next(
            (item_id for item_id, session in self.session_items.items() if session["session_id"] == selected_session_id),
            first_session_item,
        )
        if selected_item:
            self.session_tree.selection_set(selected_item)
            self.session_tree.see(selected_item)
            self._show_session(self.session_items[selected_item])
        else:
            self.current_session = None
            self.session_title.configure(text="회차를 선택하세요")
            self.session_subtitle.configure(text="")
            self.audio_button.configure(state="disabled", text="음성 파일 없음")
            for label in self.metric_labels.values():
                label.configure(text="미평가")
            self._put_text(self.transcript_view, "저장된 기록이 없습니다.")
            self._put_text(self.evaluation_view, "저장된 기록이 없습니다.")
            self._put_text(self.recommendation_view, "저장된 기록이 없습니다.")
            self._put_text(self.assistance_view, "저장된 기록이 없습니다.")

    def _select_session(self, _event: object = None) -> None:
        selection = self.session_tree.selection()
        if selection and selection[0] in self.session_items:
            self._show_session(self.session_items[selection[0]])
        else:
            self.delete_session_button.configure(state="disabled")

    def _delete_selected_session(self) -> None:
        session = getattr(self, "current_session", None)
        if not session:
            return
        name = str(session["participant_name"])
        round_label = str(session["round_label"])
        if not messagebox.askyesno(
            "면접 기록 삭제",
            f"{name} · {round_label} 기록을 삭제할까요?\n\n전사, 평가, 음성 파일, 실시간 조언, 화자 구간과 이 회차에 연결된 추천 질문 파일이 함께 삭제됩니다. 이 작업은 되돌릴 수 없습니다.",
            parent=self,
        ):
            return

        participant_id = str(session["participant_id"])
        sessions_root = (RECORDS / participant_id / "sessions").resolve()
        session_folder = Path(session["folder"]).resolve()
        if session_folder.parent != sessions_root or session_folder == sessions_root:
            messagebox.showerror("삭제할 수 없음", "선택한 기록 경로가 참가자의 세션 폴더와 일치하지 않습니다.", parent=self)
            return

        try:
            shutil.rmtree(session_folder)
        except OSError as error:
            messagebox.showerror("삭제 실패", f"기록을 삭제하지 못했습니다.\n{error}", parent=self)
            return

        recommendation_path = RECORDS / participant_id / "question-recommendations" / f"after-{session['session_id']}.md"
        recommendation_error = None
        try:
            if recommendation_path.is_file():
                recommendation_path.unlink()
            recommendation_index = recommendation_path.parent / "INDEX.md"
            if recommendation_index.is_file():
                lines = recommendation_index.read_text(encoding="utf-8").splitlines()
                retained = [line for line in lines if recommendation_path.name not in line]
                if retained != lines:
                    recommendation_index.write_text("\n".join(retained).rstrip() + "\n", encoding="utf-8")
        except OSError as error:
            recommendation_error = error

        self.sessions = find_sessions()
        self._refresh_list()
        if recommendation_error:
            messagebox.showwarning("일부 파일 삭제 실패", f"회차 기록은 삭제했지만 연결된 추천 질문 파일 또는 목차를 삭제하지 못했습니다.\n{recommendation_error}", parent=self)
        self.status.configure(text=f"{name} · {round_label} 기록을 삭제했습니다.")

    @staticmethod
    def _put_text(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content.strip() or "저장된 내용이 없습니다.")
        widget.configure(state="disabled")
        widget.yview_moveto(0)

    def _show_session(self, session: dict[str, object]) -> None:
        self.current_session = session
        self.delete_session_button.configure(state="normal")
        self.session_title.configure(text=f"{session['participant_name']}  ·  {session['round_label']}")
        self.session_subtitle.configure(text=str(session["date"]))
        for key in self.metric_labels:
            value = str(session[key])
            self.metric_labels[key].configure(text=value if value and value != "기록 없음" else "미평가")
        self._put_text(self.transcript_view, str(session["transcript"]))
        self._put_text(self.evaluation_view, str(session["evaluation"]) or "이 회차의 평가 파일이 없습니다.")
        self._put_text(self.recommendation_view, str(session["recommendation"]))
        self._put_text(self.assistance_view, str(session["assistance"]))
        audio_files = session["audio_files"]
        self.audio_button.configure(state="normal" if audio_files else "disabled",
                                    text=f"음성 파일 열기 ({len(audio_files)})" if audio_files else "음성 파일 없음")

    def _open_audio(self) -> None:
        session = getattr(self, "current_session", None)
        if not session:
            return
        audio_files = session["audio_files"]
        if not audio_files:
            return
        if len(audio_files) == 1:
            self._open_path(audio_files[0])
            return
        chooser = tk.Toplevel(self)
        chooser.title("재생할 녹음 선택")
        chooser.transient(self)
        chooser.grab_set()
        ttk.Label(chooser, text="열 녹음 파일을 선택하세요.", padding=12).pack(anchor="w")
        for path in audio_files:
            ttk.Button(chooser, text=path.name, command=lambda p=path: (chooser.destroy(), self._open_path(p))).pack(fill="x", padx=12, pady=4)

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                import os
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as error:
            messagebox.showerror("파일을 열 수 없습니다", str(error), parent=self)

    def _reload(self) -> None:
        self.sessions = find_sessions()
        self.live_people = list_participants()
        self.live_participant_entry.configure(values=sorted(self.live_people.values(), key=str.casefold))
        self._refresh_list()


if __name__ == "__main__":
    InterviewApp().mainloop()
