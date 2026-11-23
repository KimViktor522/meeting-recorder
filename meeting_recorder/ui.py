"""Интерфейс на tkinter: окно настроек, запрос «Записать?», приветствие. Весь tk живёт в одном потоке (UI)."""
import copy
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import about, ics, llm, models, settings
from .i18n import tr


def csv(text):
    return [x.strip() for x in text.split(",") if x.strip()]


class UI(threading.Thread):
    """Поток-хозяин tk: остальные потоки ставят задачи через post()."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app, self.q, self.ready = app, queue.Queue(), threading.Event()
        self.settings_win = None

    def post(self, fn, *args):
        self.q.put((fn, args))

    def run(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.ready.set()
        self._poll()
        self.root.mainloop()

    def _poll(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception as e:  # ошибка окна не должна убить рекордер
                    self.app.log(f"UI error: {e!r}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ---- публичные точки входа (из любого потока) ----
    def open_settings(self, tab=0):
        self.post(self._open_settings, tab)

    def ask(self, title, on_result):
        self.post(self._ask, title, on_result)

    def welcome(self):
        self.post(self._welcome)

    def open_diag(self):
        self.post(self._open_diag)

    def open_import(self):
        self.post(self._open_import)

    # ---- реализация (в потоке UI) ----
    def _t(self):
        return tr(settings.ui_language(self.app.cfg))

    def _open_settings(self, tab=0):
        if self.settings_win and self.settings_win.win.winfo_exists():
            self.settings_win.win.deiconify()
            self.settings_win.win.lift()
            self.settings_win.nb.select(tab)
            return
        self.settings_win = SettingsWindow(self, tab)

    def _open_diag(self):
        if getattr(self, "diag_win", None) and self.diag_win.win.winfo_exists():
            self.diag_win.win.deiconify()
            self.diag_win.win.lift()
            return
        self.diag_win = DiagWindow(self)

    def _open_import(self):
        ImportDialog(self)

    def _ask(self, title, on_result):
        t, cfg = self._t(), self.app.cfg
        win = tk.Toplevel(self.root)
        win.title(t("ask_title"))
        win.attributes("-topmost", True)
        win.geometry("+80+80")
        label = tk.Label(win, padx=20, pady=12, justify="left")
        label.pack()
        done, left, default = [], [cfg["ask_timeout_sec"]], cfg["default_on_timeout"]

        def finish(choice):
            if not done:
                done.append(choice)
                win.destroy()
                on_result(choice)

        row = tk.Frame(win)
        row.pack(pady=(0, 12))
        tk.Button(row, text=t("ask_record"), width=14, command=lambda: finish("record")).pack(side="left", padx=6)
        tk.Button(row, text=t("ask_skip"), width=14, command=lambda: finish("skip")).pack(side="left", padx=6)

        def tick():
            if done:
                return
            label.config(text=t("ask_text", title, left[0], t("act_" + default)))
            if left[0] <= 0:
                finish(default)
            else:
                left[0] -= 1
                win.after(1000, tick)

        win.protocol("WM_DELETE_WINDOW", lambda: finish(default))
        tick()

    def _welcome(self):
        t = self._t()
        win = tk.Toplevel(self.root)
        win.title(t("welcome_title") + " — " + about.APP_NAME)
        win.attributes("-topmost", True)
        ttk.Label(win, text=about.APP_NAME, font=("Segoe UI", 16, "bold")).pack(padx=24, pady=(18, 6))
        ttk.Label(win, text=t("welcome_text"), wraplength=420, justify="left").pack(padx=24, pady=6)
        ttk.Label(win, text=t("legal"), wraplength=420, justify="left", foreground="#a33").pack(padx=24, pady=6)

        def go():
            self.app.cfg["first_run_done"] = True
            settings.save(self.app.cfg)
            win.destroy()
            self._open_settings(0)

        ttk.Button(win, text=t("welcome_ok"), command=go).pack(pady=(6, 18))
        win.protocol("WM_DELETE_WINDOW", go)


def form_dialog(parent, title, fields, initial, t):
    """Модальная форма. fields: (ключ, подпись, тип text|list|combo|path, варианты). Возвращает dict или None."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    vars_ = {}
    for i, (key, label, kind, opts) in enumerate(fields):
        ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=10, pady=5)
        val = initial.get(key, "")
        if kind == "list":
            val = ", ".join(val)
        v = tk.StringVar(value=val)
        vars_[key] = (v, kind, opts)
        if kind == "combo":
            labels = [lab for _, lab in opts]
            cb = ttk.Combobox(win, state="readonly", values=labels, width=38)
            cur = next((lab for code, lab in opts if code == val), labels[0] if labels else "")
            cb.set(cur)
            cb.grid(row=i, column=1, padx=10, pady=5, sticky="we")
            vars_[key] = (cb, kind, opts)
        else:
            ttk.Entry(win, textvariable=v, width=40).grid(row=i, column=1, padx=10, pady=5, sticky="we")
            if kind == "path":
                ttk.Button(win, text=t("browse"), command=lambda v=v: v.set(filedialog.askdirectory(parent=win) or v.get())).grid(row=i, column=2, padx=(0, 10))
    result = {}

    def ok():
        for key, (w, kind, opts) in vars_.items():
            if kind == "combo":
                lab = w.get()
                result[key] = next((code for code, l in opts if l == lab), "")
            else:
                result[key] = csv(w.get()) if kind == "list" else w.get().strip()
        win.destroy()

    bar = ttk.Frame(win)
    bar.grid(row=len(fields), column=0, columnspan=3, pady=10)
    ttk.Button(bar, text=t("ok"), command=ok).pack(side="left", padx=6)
    ttk.Button(bar, text=t("cancel"), command=lambda: (result.clear(), win.destroy())).pack(side="left", padx=6)
    win.wait_window()
    return dict(result) or None


class CrudTable(ttk.Frame):
    """Таблица с кнопками Добавить/Изменить/Удалить/Выше/Ниже. items — список dict (меняется на месте)."""

    def __init__(self, parent, t, columns, items, row_fn, edit_fn):
        super().__init__(parent)
        self.t, self.items, self.row_fn, self.edit_fn = t, items, row_fn, edit_fn
        self.tree = ttk.Treeview(self, columns=[c for c, _, _ in columns], show="headings", height=9, selectmode="browse")
        for c, label, w in columns:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=1, sticky="n", padx=(8, 0))
        for text, cmd in (("add", self.add), ("edit", self.edit), ("delete", self.delete), ("up", lambda: self.move(-1)), ("down", lambda: self.move(1))):
            ttk.Button(bar, text=t(text), command=cmd).pack(fill="x", pady=2)
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.refresh()

    def refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i), values=self.row_fn(it))
        if select is not None and self.items:
            self.tree.selection_set(str(max(0, min(select, len(self.items) - 1))))

    def _sel(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

    def add(self):
        it = self.edit_fn(None)
        if it:
            self.items.append(it)
            self.refresh(len(self.items) - 1)

    def edit(self):
        i = self._sel()
        if i is not None:
            it = self.edit_fn(self.items[i])
            if it:
                self.items[i] = it
                self.refresh(i)

    def delete(self):
        i = self._sel()
        if i is not None and messagebox.askyesno(about.APP_NAME, self.t("confirm_delete"), parent=self):
            del self.items[i]
            self.refresh(i)

    def move(self, d):
        i = self._sel()
        if i is not None and 0 <= i + d < len(self.items):
            self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
            self.refresh(i + d)


class SettingsWindow:
    def __init__(self, ui, tab=0):
        self.ui, self.app = ui, ui.app
        self.cfg = copy.deepcopy(self.app.cfg)
        self.t = tr(settings.ui_language(self.cfg))
        t = self.t
        self.win = tk.Toplevel(ui.root)
        self.win.title(f"{about.APP_NAME} — {t('title')}")
        self.win.geometry("760x600")
        self.nb = ttk.Notebook(self.win)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        for build, key in ((self.tab_general, "tab_general"), (self.tab_rules, "tab_rules"), (self.tab_projects, "tab_projects"),
                           (self.tab_greeting, "tab_greeting"), (self.tab_calendar, "tab_calendar"), (self.tab_recog, "tab_recog"), (self.tab_protocol, "tab_protocol"),
                           (self.tab_about, "tab_about")):
            frame = ttk.Frame(self.nb, padding=12)
            self.nb.add(frame, text=t(key))
            build(frame)
        self.nb.select(tab)
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        self.msg = ttk.Label(bar, text="")
        self.msg.pack(side="left")
        ttk.Button(bar, text=t("cancel"), command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(bar, text=t("apply"), command=self.apply).pack(side="right", padx=4)
        ttk.Button(bar, text=t("save"), command=lambda: self.apply() and self.win.destroy()).pack(side="right", padx=4)

    # ---------- Основные ----------
    def tab_general(self, f):
        t, c = self.t, self.cfg
        f.columnconfigure(1, weight=1)
        self.out = tk.StringVar(value=c["output_dir"])
        self.auto = tk.BooleanVar(value=self.app.autostart_enabled())
        self.min_s = tk.StringVar(value=str(c["min_record_seconds"]))
        self.ask_s = tk.StringVar(value=str(c["ask_timeout_sec"]))
        self.services = tk.StringVar(value=", ".join(c["services"]))
        self.ignore = tk.StringVar(value=", ".join(c["ignore_mic_apps"]))
        acts = [("ask", t("act_ask")), ("record", t("act_record")), ("skip", t("act_skip"))]
        self.def_act = self._combo(f, 2, t("default_action"), acts, c["default_action"])
        self.def_to = self._combo(f, 4, t("default_on_timeout"), acts[1:], c["default_on_timeout"])
        self.lang_cb = self._combo(f, 0, t("language"), [("auto", t("lang_auto")), ("ru", "Русский"), ("en", "English")], c["language"])
        ttk.Label(f, text=t("output_dir")).grid(row=1, column=0, sticky="w", pady=5)
        row = ttk.Frame(f)
        row.grid(row=1, column=1, sticky="we")
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.out).grid(row=0, column=0, sticky="we")
        ttk.Button(row, text=t("browse"), command=lambda: self.out.set(filedialog.askdirectory(parent=self.win) or self.out.get())).grid(row=0, column=1, padx=4)
        ttk.Label(f, text=t("output_hint"), foreground="gray").grid(row=1, column=2, sticky="w", padx=6)
        ttk.Label(f, text=t("ask_timeout")).grid(row=3, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=5, to=300, textvariable=self.ask_s, width=8).grid(row=3, column=1, sticky="w")
        ttk.Label(f, text=t("min_seconds")).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Spinbox(f, from_=0, to=3600, textvariable=self.min_s, width=8).grid(row=5, column=1, sticky="w")
        ttk.Label(f, text=t("services")).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Entry(f, textvariable=self.services).grid(row=7, column=0, columnspan=3, sticky="we")
        ttk.Label(f, text=t("ignore_apps")).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Entry(f, textvariable=self.ignore).grid(row=9, column=0, columnspan=3, sticky="we")
        ttk.Checkbutton(f, text=t("autostart"), variable=self.auto).grid(row=10, column=0, columnspan=3, sticky="w", pady=14)

    def _combo(self, f, row, label, options, current, label_widget=True):
        if label_widget and label:
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=5)
        cb = ttk.Combobox(f, state="readonly", values=[lab for _, lab in options], width=46)
        cb.set(next((lab for code, lab in options if code == current), options[0][1]))
        cb.grid(row=row, column=1, sticky="w")
        cb.options = options
        return cb

    @staticmethod
    def _code(cb):
        return next((code for code, lab in cb.options if lab == cb.get()), cb.options[0][0])

    # ---------- Правила ----------
    def tab_rules(self, f):
        t = self.t
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text=t("rule_hint"), wraplength=680, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 8))
        act = lambda a: t("act_" + a)
        self.rules_tbl = CrudTable(f, t, [("n", t("rule_name"), 170), ("k", t("rule_keywords"), 250), ("a", t("rule_action"), 100), ("p", t("rule_project"), 130)],
                                   self.cfg["rules"], lambda r: (r["name"], ", ".join(r["match"]), act(r["action"]), r.get("project") or ""), self.edit_rule)
        self.rules_tbl.grid(row=1, column=0, sticky="nsew")

    def edit_rule(self, rule):
        t = self.t
        projects = [("", t("no_project"))] + [(p["name"], p["name"]) for p in self.cfg["projects"]]
        fields = [("name", t("rule_name"), "text", None), ("match", t("rule_keywords"), "list", None),
                  ("action", t("rule_action"), "combo", [("record", t("act_record")), ("skip", t("act_skip"))]),
                  ("project", t("rule_project"), "combo", projects)]
        r = form_dialog(self.win, t("tab_rules"), fields, rule or {"action": "record", "match": []}, t)
        return r if r and r["match"] else None

    # ---------- Проекты ----------
    def tab_projects(self, f):
        t = self.t
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text=t("proj_hint"), wraplength=680, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.proj_tbl = CrudTable(f, t, [("n", t("proj_name"), 150), ("p", t("proj_folder"), 280), ("k", t("proj_keywords"), 230)],
                                  self.cfg["projects"], lambda p: (p["name"], p.get("path", ""), ", ".join(p.get("keywords", []))), self.edit_project)
        self.proj_tbl.grid(row=1, column=0, sticky="nsew")

    def edit_project(self, proj):
        t = self.t
        fields = [("name", t("proj_name"), "text", None), ("path", t("proj_folder"), "path", None), ("keywords", t("proj_keywords"), "list", None)]
        r = form_dialog(self.win, t("tab_projects"), fields, proj or {"keywords": []}, t)
        return r if r and r["name"] else None

    # ---------- Приветствие ----------
    def tab_greeting(self, f):
        t, g = self.t, self.cfg["greeting"]
        f.columnconfigure(0, weight=1)
        self.g_on = tk.BooleanVar(value=g["enabled"])
        self.g_phr = tk.StringVar(value=", ".join(g["phrases"]))
        self.g_fb = tk.StringVar(value=str(g["fallback_min"]))
        self.g_model = tk.StringVar(value=g["model"])
        ttk.Checkbutton(f, text=t("greet_enable"), variable=self.g_on).grid(row=0, column=0, sticky="w")
        ttk.Label(f, text=t("greet_hint"), wraplength=680, justify="left", foreground="gray").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Label(f, text=t("greet_phrases")).grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.g_phr).grid(row=3, column=0, sticky="we")
        ttk.Label(f, text=t("greet_fallback")).grid(row=4, column=0, sticky="w", pady=(12, 2))
        ttk.Spinbox(f, from_=0, to=60, textvariable=self.g_fb, width=8).grid(row=5, column=0, sticky="w")
        ttk.Label(f, text=t("greet_model")).grid(row=6, column=0, sticky="w", pady=(12, 2))
        ttk.Combobox(f, textvariable=self.g_model, values=["tiny", "base", "small"], width=12, state="readonly").grid(row=7, column=0, sticky="w")

    # ---------- Календарь ----------
    def tab_calendar(self, f):
        t, c = self.t, self.cfg["calendar"]
        f.columnconfigure(0, weight=1)
        self.cal_on = tk.BooleanVar(value=c["enabled"])
        self.cal_url = tk.StringVar(value=settings.unprotect(c.get("ics_url_enc", "")))
        ttk.Checkbutton(f, text=t("cal_enable"), variable=self.cal_on).grid(row=0, column=0, sticky="w")
        ttk.Label(f, text=t("cal_url")).grid(row=1, column=0, sticky="w", pady=(12, 2))
        ttk.Entry(f, textvariable=self.cal_url, show="•").grid(row=2, column=0, sticky="we")
        ttk.Label(f, text=t("cal_help"), wraplength=680, justify="left", foreground="gray").grid(row=3, column=0, sticky="w", pady=10)
        ttk.Button(f, text=t("test"), command=self.test_calendar).grid(row=4, column=0, sticky="w")
        self.cal_msg = ttk.Label(f, text="", wraplength=680, justify="left")
        self.cal_msg.grid(row=5, column=0, sticky="w", pady=8)

    def test_calendar(self):
        t, url = self.t, self.cal_url.get().strip()
        self.cal_msg.config(text=t("testing"))

        def work():
            try:
                ev = ics.current_event(ics.fetch(url, ttl=0))
                msg = t("cal_found", ev["title"]) if ev else t("cal_none")
            except Exception as e:
                msg = f"{t('error')}: {str(e)[:250]}"
            self.ui.post(lambda: self.cal_msg.config(text=msg))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Распознавание ----------
    def tab_recog(self, f):
        t, w = self.t, self.cfg["whisper"]
        f.columnconfigure(0, weight=1)
        self.model = tk.StringVar(value=w["model"])
        ttk.Label(f, text=t("recog_hint"), wraplength=680, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.mtree = ttk.Treeview(f, columns=("m", "s", "sp", "q", "st"), show="headings", height=6, selectmode="browse")
        for c, label, wd in (("m", t("model"), 150), ("s", t("size"), 80), ("sp", t("speed"), 100), ("q", t("quality"), 100), ("st", t("status"), 180)):
            self.mtree.heading(c, text=label)
            self.mtree.column(c, width=wd)
        self.mtree.grid(row=1, column=0, columnspan=2, sticky="we")
        bar = ttk.Frame(f)
        bar.grid(row=2, column=0, sticky="w", pady=8)
        ttk.Button(bar, text=t("use_model"), command=self.use_model).pack(side="left", padx=(0, 6))
        self.dl_btn = ttk.Button(bar, text=t("download"), command=self.download_model)
        self.dl_btn.pack(side="left")
        self.recog_msg = ttk.Label(f, text="")
        self.recog_msg.grid(row=3, column=0, columnspan=2, sticky="w")
        langs = [("auto", t("lang_auto")), ("ru", "Русский"), ("en", "English"), ("uk", "Українська"), ("kk", "Қазақша"), ("de", "Deutsch"),
                 ("fr", "Français"), ("es", "Español"), ("it", "Italiano"), ("pt", "Português"), ("tr", "Türkçe"), ("zh", "中文")]
        ttk.Label(f, text=t("w_lang")).grid(row=4, column=0, sticky="w", pady=(14, 2))
        self.w_lang = self._combo(f, 5, "", langs, w.get("language", "auto"))
        self.w_lang.grid(row=5, column=0, sticky="w")
        self.installed = {}
        self.refresh_models()
        threading.Thread(target=self._scan_models, daemon=True).start()

    def _scan_models(self):
        res = {m[0]: models.is_downloaded(m[0]) for m in models.CATALOG}
        self.ui.post(lambda: (self.installed.update(res), self.refresh_models()))

    def refresh_models(self):
        t = self.t
        self.mtree.delete(*self.mtree.get_children())
        for name, size, speed, quality in models.CATALOG:
            st = t("installed") if self.installed.get(name) else t("not_installed")
            if name == self.model.get():
                st += " · " + t("selected")
            self.mtree.insert("", "end", iid=name, values=(name, size, "●" * speed, "●" * quality, st))

    def _picked(self):
        s = self.mtree.selection()
        return s[0] if s else None

    def use_model(self):
        if self._picked():
            self.model.set(self._picked())
            self.refresh_models()

    def download_model(self):
        name, t = self._picked(), self.t
        if not name:
            return
        self.dl_btn.state(["disabled"])
        self.recog_msg.config(text=t("downloading", name))

        def work():
            try:
                models.download(name)
                self.installed[name] = True
                msg = t("download_done", name)
            except Exception as e:
                msg = f"{t('error')}: {str(e)[:250]}"
            self.ui.post(lambda: (self.recog_msg.config(text=msg), self.dl_btn.state(["!disabled"]), self.refresh_models()))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Протокол ----------
    def tab_protocol(self, f):
        t, p = self.t, self.cfg["protocol"]
        f.columnconfigure(1, weight=1)
        provs = [(k, t("prov_" + k)) for k in ("none", "zai", "openai", "openrouter", "ollama", "custom", "anthropic", "claude_cli")]
        ttk.Label(f, text=t("provider")).grid(row=0, column=0, sticky="w", pady=5)
        self.prov = self._combo(f, 0, "", provs, p["provider"])
        self.prov.bind("<<ComboboxSelected>>", self.on_provider)
        self.p_url, self.p_model, self.p_key = tk.StringVar(value=p["base_url"]), tk.StringVar(value=p["model"]), tk.StringVar()
        self.key_stored = settings.unprotect(p.get("api_key_enc", ""))
        self.clear_key = False
        for r, (label, var, show) in enumerate(((t("base_url"), self.p_url, None), (t("model_name"), self.p_model, None), (t("api_key"), self.p_key, "•")), 1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=5)
            ttk.Entry(f, textvariable=var, show=show or "").grid(row=r, column=1, sticky="we")
        self.key_lbl = ttk.Label(f, text=t("key_saved") if self.key_stored else "", foreground="gray")
        self.key_lbl.grid(row=4, column=1, sticky="w")
        ttk.Button(f, text=t("key_clear"), command=self.drop_key).grid(row=4, column=2, padx=6)
        ttk.Label(f, text=t("privacy"), wraplength=680, foreground="#a33", justify="left").grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        row = ttk.Frame(f)
        row.grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Button(row, text=t("test"), command=self.test_protocol).pack(side="left")
        self.prot_msg = ttk.Label(row, text="", wraplength=520)
        self.prot_msg.pack(side="left", padx=10)
        ttk.Label(f, text=t("prompt") + "  " + t("prompt_hint")).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self.prompt = tk.Text(f, height=9, wrap="word")
        self.prompt.insert("1.0", p.get("prompt", ""))
        self.prompt.grid(row=8, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(8, weight=1)

    def on_provider(self, _=None):
        url, model = settings.PRESETS.get(self._code(self.prov), ("", ""))
        self.p_url.set(url)
        self.p_model.set(model)

    def drop_key(self):
        self.key_stored, self.clear_key = "", True
        self.p_key.set("")
        self.key_lbl.config(text="")

    def _protocol_cfg(self):
        key = self.p_key.get().strip() or self.key_stored
        return {"protocol": {"provider": self._code(self.prov), "base_url": self.p_url.get().strip(), "model": self.p_model.get().strip(),
                             "api_key_enc": settings.protect(key), "prompt": ""}}

    def test_protocol(self):
        t, cfg = self.t, self._protocol_cfg()
        self.prot_msg.config(text=t("testing"))

        def work():
            ok, msg = llm.test(cfg)
            self.ui.post(lambda: self.prot_msg.config(text=("✔ " if ok else "✖ ") + msg))

        threading.Thread(target=work, daemon=True).start()

    # ---------- О программе ----------
    def tab_about(self, f):
        t = self.t
        ttk.Label(f, text=about.APP_NAME, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(f, text=t("about_desc"), wraplength=680, justify="left").pack(anchor="w", pady=10)
        for label, val in ((t("version"), about.VERSION), (t("author"), about.AUTHOR), (t("contact"), about.CONTACT), (t("license"), about.LICENSE)):
            ttk.Label(f, text=f"{label}: {val}").pack(anchor="w")
        link = ttk.Label(f, text=f"{t('repo')}: {about.URL}", foreground="#0645ad", cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open(about.URL))
        ttk.Label(f, text=t("legal"), wraplength=680, justify="left", foreground="#a33").pack(anchor="w", pady=16)
        ttk.Button(f, text=t("open_logs"), command=lambda: os.startfile(settings.data_dir())).pack(anchor="w")

    # ---------- Сохранение ----------
    def collect(self):
        c = self.cfg
        c["language"] = self._code(self.lang_cb)
        c["output_dir"] = self.out.get().strip()
        c["default_action"], c["default_on_timeout"] = self._code(self.def_act), self._code(self.def_to)
        c["ask_timeout_sec"] = max(5, int(self.ask_s.get() or 30))
        c["min_record_seconds"] = max(0, int(self.min_s.get() or 0))
        c["services"], c["ignore_mic_apps"] = csv(self.services.get()), csv(self.ignore.get())
        c["calendar"] = {"enabled": self.cal_on.get(), "ics_url_enc": settings.protect(self.cal_url.get().strip())}
        c["greeting"] = {"enabled": self.g_on.get(), "phrases": csv(self.g_phr.get()), "fallback_min": max(0, int(self.g_fb.get() or 0)),
                         "model": self.g_model.get()}
        c["whisper"] = {"model": self.model.get(), "language": self._code(self.w_lang)}
        key = self.p_key.get().strip() or self.key_stored
        c["protocol"] = {"provider": self._code(self.prov), "base_url": self.p_url.get().strip(), "model": self.p_model.get().strip(),
                         "api_key_enc": settings.protect(key) if key else "", "prompt": self.prompt.get("1.0", "end").strip()}
        c["first_run_done"] = True
        return c

    def apply(self):
        try:
            cfg = self.collect()
        except ValueError:
            messagebox.showerror(about.APP_NAME, self.t("error"), parent=self.win)
            return False
        self.app.apply_config(cfg, autostart=self.auto.get())
        self.msg.config(text=self.t("saved"))
        return True


class DiagWindow:
    """Живые уровни, ручные кнопки, события и снимок детектора."""

    def __init__(self, ui):
        self.ui, self.app = ui, ui.app
        self.t = t = tr(settings.ui_language(self.app.cfg))
        self.win = tk.Toplevel(ui.root)
        self.win.title(f"{about.APP_NAME} — {t('diag_title')}")
        self.win.geometry("640x520")
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(7, weight=1)
        self.status = ttk.Label(f, text="", font=("Segoe UI", 10, "bold"))
        self.status.grid(row=0, column=0, columnspan=3, sticky="w")
        self.bars = {}
        for r, key in ((1, "diag_mic"), (2, "diag_system")):
            ttk.Label(f, text=t(key)).grid(row=r, column=0, sticky="w", pady=4)
            bar = ttk.Progressbar(f, maximum=100, length=300)
            bar.grid(row=r, column=1, sticky="we", padx=8)
            self.bars[key] = bar
        ttk.Label(f, text=t("diag_hint"), wraplength=600, foreground="gray", justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        row = ttk.Frame(f)
        row.grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        self.start_btn = ttk.Button(row, text=t("diag_start"), command=self.app.start_manual)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(row, text=t("diag_stop"), command=self.app.stop_manual)
        self.stop_btn.pack(side="left", padx=(0, 6))
        ttk.Button(row, text=t("diag_snapshot"), command=self.snapshot).pack(side="left")
        ttk.Label(f, text=t("diag_events")).grid(row=5, column=0, sticky="w", pady=(8, 2))
        self.log = tk.Text(f, height=12, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.shown = 0
        self.tick()

    def snapshot(self):
        text = capture_describe(self.app.cfg)
        self.append(text)

    def append(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + chr(10))
        self.log.see("end")
        self.log.config(state="disabled")

    def tick(self):
        if not self.win.winfo_exists():
            return
        s, t = self.app.session, self.t
        lv = self.app.levels()
        self.status.config(text=t("diag_rec", s.meta["title"]) if s else t("diag_idle"))
        for key, val in zip(("diag_mic", "diag_system"), lv or (0, 0)):
            self.bars[key]["value"] = min(100, (val ** 0.5) * 100)  # корень: тихая речь тоже заметна
        self.start_btn.state(["disabled"] if s else ["!disabled"])
        self.stop_btn.state(["!disabled"] if s else ["disabled"])
        events = list(self.app.events)
        for ts, text in events[self.shown:]:
            self.append(f"{ts}  {text}")
        self.shown = len(events)
        self.win.after(250, self.tick)


def capture_describe(cfg):
    from . import capture
    return capture.describe(cfg, settings.ui_language(cfg))


class ImportDialog:
    """Импорт готового видео/аудио/транскрипта: диалог -> очередь обработки."""

    def __init__(self, ui):
        self.ui, self.app = ui, ui.app
        self.t = t = tr(settings.ui_language(self.app.cfg))
        self.win = tk.Toplevel(ui.root)
        self.win.title(f"{about.APP_NAME} — {t('import_title')}")
        self.win.attributes("-topmost", True)
        f = ttk.Frame(self.win, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text=t("import_hint"), wraplength=520, justify="left", foreground="gray").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.media, self.trans = tk.StringVar(), tk.StringVar()
        self.title_v, self.date_v = tk.StringVar(), tk.StringVar()
        self.keep = tk.BooleanVar()
        mt = [("Media", "*.mp4 *.mkv *.mov *.avi *.webm *.m4a *.mp3 *.wav *.ogg *.opus *.aac *.flac *.wma"), ("All", "*.*")]
        for r, (label, var, types) in enumerate(((t("import_media"), self.media, mt),
                                                 (t("import_transcript"), self.trans, [("Transcript", "*.vtt *.srt *.txt *.md"), ("All", "*.*")])), 1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(f, textvariable=var, width=46).grid(row=r, column=1, sticky="we", padx=6)
            ttk.Button(f, text=t("browse"), command=lambda v=var, ty=types: self.pick(v, ty)).grid(row=r, column=2)
        for r, (label, var) in enumerate(((t("import_name"), self.title_v), (t("import_date"), self.date_v)), 3):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ttk.Entry(f, textvariable=var).grid(row=r, column=1, sticky="we", padx=6)
        ttk.Label(f, text=t("import_project")).grid(row=5, column=0, sticky="w", pady=4)
        names = [p["name"] for p in self.app.cfg["projects"]]
        self.proj = ttk.Combobox(f, state="readonly", values=[t("import_auto")] + names)
        self.proj.current(0)
        self.proj.grid(row=5, column=1, sticky="we", padx=6)
        ttk.Checkbutton(f, text=t("import_keep"), variable=self.keep).grid(row=6, column=0, columnspan=3, sticky="w", pady=6)
        self.msg = ttk.Label(f, text="", wraplength=520, justify="left")
        self.msg.grid(row=7, column=0, columnspan=3, sticky="w")
        bar = ttk.Frame(f)
        bar.grid(row=8, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(bar, text=t("import_go"), command=self.go).pack(side="left", padx=6)
        ttk.Button(bar, text=t("cancel"), command=self.win.destroy).pack(side="left", padx=6)

    def pick(self, var, types):
        path = filedialog.askopenfilename(parent=self.win, filetypes=types)
        if path:
            var.set(path)
            if not self.title_v.get():
                self.title_v.set(os.path.splitext(os.path.basename(path))[0])

    def go(self):
        t = self.t
        media, trans = self.media.get().strip(), self.trans.get().strip()
        if not media and not trans:
            self.msg.config(text=t("import_need"))
            return
        project = self.proj.get() if self.proj.current() > 0 else ""
        try:
            self.app.import_files(media, trans, self.title_v.get().strip(), self.date_v.get().strip(), project, self.keep.get())
        except Exception as e:
            self.msg.config(text=f"{t('error')}: {e}")
            return
        self.msg.config(text=t("import_queued"))
        self.win.after(2500, self.win.destroy)
