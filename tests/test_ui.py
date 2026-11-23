"""Дымовой тест окна настроек (нужен рабочий стол Windows): python tests/test_ui.py"""
import os
import pathlib
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["MR_HOME"] = tempfile.mkdtemp(prefix="mr_ui_")

from meeting_recorder import settings, ui  # noqa: E402


class AppStub:
    def __init__(self):
        self.cfg = settings.load()
        self.auto = None
        self.session, self.events, self.imported, self.started = None, [("12:00:00", "WARN mic_silent")], [], []

    def levels(self):
        return None

    def start_manual(self):
        self.started.append(1)

    def stop_manual(self):
        pass

    def import_files(self, *a):
        self.imported.append(a)

    def log(self, m):
        print("LOG", m)

    def autostart_enabled(self):
        return False

    def apply_config(self, cfg, autostart):
        settings.save(cfg)
        self.cfg, self.auto = cfg, autostart


app = AppStub()
u = ui.UI(app)
u.start()
u.ready.wait(10)
done, err = threading.Event(), []


def scenario():
    try:
        u._open_settings(0)
        w = u.settings_win
        assert len(w.nb.tabs()) == 8  # все вкладки построены (в т.ч. каталог моделей, протокол, календарь, о программе)
        w.cfg["projects"].append({"name": "P1", "path": "", "keywords": ["alpha"]})
        w.cfg["rules"].append({"name": "r", "match": ["x"], "action": "skip", "project": ""})
        w.rules_tbl.refresh()
        w.model.set("base")
        w.auto.set(True)
        w.cal_on.set(True)
        w.cal_url.set("https://example.com/secret.ics")
        w.p_key.set("sk-secret")
        w.prov.set(w.t("prov_openai"))
        w.on_provider()
        assert w.apply()
        # переоткрытие показывает сохранённое
        w.win.destroy()
        u._open_settings(0)
        assert u.settings_win.cal_url.get() == "https://example.com/secret.ics"
        assert u.settings_win.key_stored == "sk-secret"
        u.settings_win.win.destroy()
        # окно диагностики: кнопки, события, снимок детектора
        u._open_diag()
        d = u.diag_win
        d.tick()
        d.start_btn.invoke()
        assert app.started == [1] and "WARN mic_silent" in d.log.get("1.0", "end")
        d.snapshot()
        assert "Решение" in d.log.get("1.0", "end") or "Decision" in d.log.get("1.0", "end")
        d.win.destroy()
        # диалог импорта: проверка ввода и передача параметров
        u._open_import()
        # (диалог — последний Toplevel; достаём через closure невозможно, создаём напрямую)
        dlg = ui.ImportDialog(u)
        dlg.go()
        assert not app.imported  # без файлов — отказ
        dlg.media.set("C:/x/call.mp4")
        dlg.title_v.set("T")
        dlg.proj.set("")
        dlg.go()
        assert app.imported and app.imported[0][0] == "C:/x/call.mp4" and app.imported[0][2] == "T"
        dlg.win.destroy()
    except Exception as e:  # noqa: BLE001
        import traceback
        err.append(traceback.format_exc())
    done.set()


u.post(scenario)
assert done.wait(30), "UI завис"
assert not err, err[0]
saved = settings.load()
assert saved["whisper"]["model"] == "base" and app.auto is True
assert saved["protocol"]["provider"] == "openai" and saved["protocol"]["base_url"].startswith("https://api.openai.com")
raw = settings.config_path().read_text(encoding="utf-8")
assert "sk-secret" not in raw and "secret.ics" not in raw  # секреты в файле зашифрованы
assert saved["projects"][0]["name"] == "P1" and saved["rules"][0]["action"] == "skip"
print("test_ui OK")
