#!/usr/bin/env python3
"""VeloxScreen native Qt Widgets alpha for Debian 12 / Raspberry Pi 4."""
import configparser
from collections import deque
import json
import os
import tempfile
import re
import sys
import time
import threading
import wifi_manager
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from functools import partial

from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal, QRect, QEasingCurve, QPropertyAnimation
from PyQt5.QtGui import QFont, QPainter, QColor, QPen, QPixmap, QLinearGradient, QBrush
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QGridLayout, QStackedWidget, QComboBox, QDoubleSpinBox, QCheckBox,
    QLineEdit, QPlainTextEdit, QMessageBox, QInputDialog, QSplitter, QSizePolicy, QDialog, QScroller, QScrollerProperties, QSlider,
    QGraphicsOpacityEffect
)

BASE = Path(__file__).resolve().parent
# Printer-specific configuration lives beside printer.cfg.
# The legacy config is read only as fallback for existing installations.
PRINTER_CONFIG_DIR = Path.home() / 'printer_data' / 'config'
CFG_PATH = PRINTER_CONFIG_DIR / 'veloxscreen.conf'
if not CFG_PATH.is_file():
    CFG_PATH = BASE / 'veloxscreen.conf'
config = configparser.ConfigParser(interpolation=None)
config.optionxform = str
config.read(CFG_PATH)


def conf(section, key, default=''):
    return config.get(section, key, fallback=default)


def save_config():
    """Save user-specific UI settings atomically without touching printer.cfg."""
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='.veloxscreen.', suffix='.tmp', dir=str(CFG_PATH.parent))
    try:
        if CFG_PATH.exists():
            os.fchmod(fd, CFG_PATH.stat().st_mode & 0o777)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            config.write(handle)
        os.replace(path, CFG_PATH)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def set_config(section, key, value):
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, str(value))
    save_config()


def options(section, key, fallback):
    values = conf(section, key, fallback)
    out = []
    for v in values.split(','):
        try:
            n = float(v.strip())
            if n > 0:
                out.append(n)
        except ValueError:
            pass
    return out or [float(x.strip()) for x in fallback.split(',')]


def clamp_override(value, minimum, maximum):
    """Bound a percentage and retain whole-percent increments."""
    return min(max(int(round(value)), minimum), maximum)


def fmt(v, digits=1):
    try:
        return f'{float(v):.{digits}f}'
    except (TypeError, ValueError):
        return '--'


def fval(v, fallback=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


class Network(QObject):
    data = pyqtSignal(object)
    err = pyqtSignal(str)
    reply = pyqtSignal(str)
    gcode_messages = pyqtSignal(object)

    def __init__(self, url):
        super().__init__()
        self.url = url.rstrip('/')
        self._running = False
        self.objects = []
        self._tick = 0
        self._gcode_running = False
        # Seed the store on startup; never pop up stale messages from a previous print.
        self._gcode_initialized = False
        self._gcode_recent = deque(maxlen=350)
        self._gcode_seen = set()
        self._script_queue = []
        self._script_cv = threading.Condition()
        threading.Thread(target=self._script_worker, daemon=True).start()

    def call(self, path, method='GET', payload=None):
        url = self.url + path
        body = urllib.parse.urlencode(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        with urllib.request.urlopen(req, timeout=3) as r:
            raw = r.read().decode('utf-8')
            return json.loads(raw) if raw else {}

    def poll(self):
        if self._running:
            return
        self._running = True

        def run():
            try:
                if not self.objects or self._tick % 30 == 0:
                    discovered = self.call('/printer/objects/list')
                    self.objects = discovered.get('result', {}).get('objects', [])
                requested = [x for x in self.objects if x in (
                    'toolhead', 'motion_report', 'gcode_move', 'print_stats',
                    'virtual_sdcard', 'heaters', 'bed_mesh',
                    'firmware_retraction', 'configfile', 'display_status')
                    or x == 'extruder' or re.fullmatch(r'extruder\d+', x)
                    or x == 'heater_bed' or x.startswith(('temperature_sensor ', 'heater_generic ',
                                                          'temperature_fan ', 'fan_generic ', 'heater_fan '))
                    or x == 'fan' or x.startswith('led_effect ')]
                query = '&'.join(urllib.parse.quote(o, safe='') for o in requested)
                payload = self.call('/printer/objects/query?' + query)
                self._tick += 1
                self.data.emit({'objects': self.objects,
                                'status': payload.get('result', {}).get('status', {}),
                                'timestamp': payload.get('result', {}).get('eventtime', 0)})
            except Exception as exc:
                self.err.emit(str(exc))
            finally:
                self._running = False
        threading.Thread(target=run, daemon=True).start()

    def poll_gcode_store(self):
        """Read Klipper G-Code responses, including messages from prints and macros.

        Moonraker's gcode_store includes RESPOND output from *all* clients,
        not only the requests sent by VeloxScreen. Requests run off the UI thread.
        """
        if self._gcode_running:
            return
        self._gcode_running = True

        def run():
            try:
                response = self.call('/server/gcode_store?count=100')
                entries = response.get('result', {}).get('gcode_store', [])
                if not isinstance(entries, list):
                    return
                fresh = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    key = (entry.get('time'), entry.get('type'), entry.get('message'))
                    if key in self._gcode_seen:
                        continue
                    if self._gcode_initialized:
                        fresh.append(entry)
                    if len(self._gcode_recent) == self._gcode_recent.maxlen:
                        self._gcode_seen.discard(self._gcode_recent.popleft())
                    self._gcode_recent.append(key)
                    self._gcode_seen.add(key)
                self._gcode_initialized = True
                if fresh:
                    self.gcode_messages.emit(fresh)
            except Exception:
                # Optional G-code store endpoint may be unavailable on some builds.
                # Never break the normal UI or spam errors if that happens.
                pass
            finally:
                self._gcode_running = False
        threading.Thread(target=run, daemon=True).start()

    def script(self, script):
        # A single FIFO worker preserves G-Code ordering, including fan slider moves.
        with self._script_cv:
            self._script_queue.append(script)
            # Commands are absolute targets: when tapped rapidly, only the
            # latest queued fan/M220/M221 target for that channel is needed.
            def coalesce_key(command):
                if re.fullmatch(r'M220 S[0-9]+', command):
                    return 'speed-override'
                if re.fullmatch(r'M221 S[0-9]+', command):
                    return 'flow-override'
                if command.startswith('M106 S'):
                    return 'fan'
                if command.startswith('SET_FAN_SPEED FAN='):
                    return 'fan:' + command.split(' FAN=', 1)[1].split(' ', 1)[0]
                return None
            key = coalesce_key(script)
            if key is not None and len(self._script_queue) > 1:
                self._script_queue = [cmd for cmd in self._script_queue[:-1]
                                      if coalesce_key(cmd) != key] + [script]
            self._script_cv.notify()

    def _script_worker(self):
        while True:
            with self._script_cv:
                while not self._script_queue:
                    self._script_cv.wait()
                script = self._script_queue.pop(0)
            try:
                response = self.call('/printer/gcode/script', 'POST', {'script': script})
                # Do not flood the console with every intermediate slider value.
                if not script.startswith(('M106 S', 'SET_FAN_SPEED FAN=')):
                    self.reply.emit('OK  ' + script.replace('\n', ' | ') + '  ' + str(response.get('result', '')))
            except Exception as exc:
                self.err.emit('G-Code: ' + str(exc))

    def load_files(self, success):
        # Callbacks are only invoked via a Qt signal from worker to GUI.
        def run():
            try:
                result = self.call('/server/files/list?root=gcodes')
                self.reply.emit('__FILES__' + json.dumps(result.get('result', [])))
            except Exception as exc:
                self.err.emit('Files: ' + str(exc))
        threading.Thread(target=run, daemon=True).start()



# Human-readable strings are localized, never Klipper object names/G-Code.
LANGUAGE = conf('appearance', 'language', 'en').strip().lower()
if LANGUAGE not in ('en', 'de'):
    LANGUAGE = 'en'

# Existing German labels are deliberately kept as translation source for backwards
# compatibility with the original alpha UI and printer config files.
_I18N = [
    ('Alle Temperaturen ›', 'All temperatures ›'),
    ('Temperaturen  ·  Übersicht', 'Temperatures · Overview'),
    ('Homing  ·  getrennte Achsen', 'Homing · Individual axes'),
    ('Temperatur-Presets  ·  veloxscreen.conf', 'Temperature presets · veloxscreen.conf'),
    ('Heizer mit Sollwert · Sensoren nur Anzeige', 'Heaters have targets · Sensors are read-only'),
    ('Extrusion nur bei ausreichend heißem, aktivem Hotend und ohne laufenden Druck. Klipper prüft zusätzlich seine Grenzwerte.', 'Extrude only when the active hotend is hot enough and no print is running. Klipper enforces its own safety limits.'),
    ('Presets beziehen sich auf das gewählte Tool, Heizbett und ggf. Kammerheizer.', 'Presets target the selected tool, bed and/or chamber heater.'),
    ('Upload folgt in der nächsten Version. Datei-Start nur nach Bestätigung.', 'Upload is planned. Start a print from a selected file.'),
    ('Befehle und Antworten, die über VeloxScreen gesendet werden. Vollständiges Moonraker-Event-Streaming folgt.', 'Commands and replies sent through VeloxScreen. Full Moonraker event streaming is planned.'),
    ('Alle gcode_macro-Einträge aus Klipper werden automatisch erkannt. Ausblenden löscht kein Makro.', 'All Klipper gcode_macro entries are detected. Hiding does not delete macros.'),
    ('Aus = Befehle werden beim Tippen direkt gesendet. Ein = Nachfrage vor jedem Befehl. Einstellung bleibt gespeichert.', 'Off: commands are sent directly. On: ask before each command. The setting is saved.'),
    ('Neustart direkt auf dem Touchdisplay. Alternativ über Mainsail → Service Control → veloxscreen (nach einmaliger Einrichtung).', 'Restart the touch application only. You can also use Mainsail → Service Control → veloxscreen.'),
    ('Makros ein-/ausblenden unter Makros. Die Einstellungen werden in veloxscreen.conf gespeichert.', 'Show/hide macros in Macros. Settings are saved to veloxscreen.conf.'),
    ('Keine passenden regelbaren Heizungen für dieses Preset gefunden.', 'No matching controllable heaters for this preset.'),
    ('Keine [firmware_retraction]-Sektion in der geladenen Klipper-Konfiguration gefunden.', 'No [firmware_retraction] section in the loaded Klipper configuration.'),
    ('Manuelles Extrudieren während Druck/Pause gesperrt.', 'Manual extrusion is disabled while printing or paused.'),
    ('Wähle den tatsächlich aktiven Extruder. Toolwechsel über konfigurierte T-Makros.', 'Select the active extruder. Use configured T macros for tool changes.'),
    ('Automatisch geregelt · nur Anzeige', 'Automatically controlled · read-only'),
    ('Vor G-Code-Befehlen bestätigen', 'Confirm before sending G-Code'),
    ('Verbindung zu Moonraker wird hergestellt ...', 'Connecting to Moonraker ...'),
    ('Moonraker wird verbunden ...', 'Connecting to Moonraker ...'),
    ('Temperaturen werden geladen ...', 'Loading temperatures ...'),
    ('Keine passenden Heizungen oder Kammersensoren gefunden.', 'No suitable heaters or chamber sensors found.'),
    ('Keine Temperaturobjekte erkannt.', 'No temperature objects detected.'),
    ('Keine Makros gefunden oder Klipper noch nicht verbunden.', 'No macros found or Klipper is not connected yet.'),
    ('Sichtbarkeit nicht gespeichert: ', 'Visibility not saved: '),
    ('Config konnte nicht gespeichert werden: ', 'Could not save config: '),
    ('VeloxScreen jetzt neu starten?', 'Restart VeloxScreen now?'),
    ('Druckstatus', 'Print status'),
    ('Referenzierung: ', 'Homed axes: '),
    ('Referenziert: ', 'Homed: '),
    ('keine Achse', 'none'),
    ('Extruder wird erkannt ...', 'Detecting extruder ...'),
    ('Aktiver Extruder:', 'Active extruder:'),
    ('Düse:', 'Nozzle:'),
    ('Länge (mm)', 'Length (mm)'),
    ('Extra (mm)', 'Extra (mm)'),
    ('Übernehmen', 'Apply'),
    ('Abbrechen', 'Cancel'),
    ('Löschen', 'Clear'),
    ('Bitte eine Zahl eingeben.', 'Please enter a number.'),
    ('Erlaubt:', 'Allowed:'),
    ('Bereich', 'Range'),
    ('Eingabe', 'Input'),
    ('GESCHWINDIGKEIT', 'SPEED'),
    ('MATERIALFLUSS', 'FLOW'),
    ('ACCEL-LIMIT', 'ACCEL LIMIT'),
    ('Temperaturen', 'Temperatures'),
    ('Steuerung', 'Control'),
    ('Dateien', 'Files'),
    ('Kalibrierung', 'Calibration'),
    ('Konsole', 'Console'),
    ('Makros', 'Macros'),
    ('Einstellungen', 'Settings'),
    ('Tool / Extruder', 'Tool / Extruder'),
    ('Extrudieren', 'Extrude'),
    ('Zurückziehen', 'Retract'),
    ('Länge mm', 'Length mm'),
    ('Achsensteuerung', 'Axis control'),
    ('Schrittweite mm', 'Step distance mm'),
    ('Lüfter  ·  automatisch erkannt', 'Fans · Auto-detected'),
    ('G-Code-Dateien auf Moonraker', 'G-Code files on Moonraker'),
    ('Aktualisieren', 'Refresh'),
    ('Ausgewählten Druck starten', 'Start selected print'),
    ('G-Code / Makro eingeben', 'Enter G-Code / macro'),
    ('Senden', 'Send'),
    ('Makros ausführen', 'Run macros'),
    ('Sichtbarkeit · alle Makros', 'Visibility · All macros'),
    ('Warte auf Klipper-Konfiguration ...', 'Waiting for Klipper config ...'),
    ('Bestätigungsdialoge', 'Confirmation dialogs'),
    ('Service Control', 'Service Control'),
    ('VeloxScreen neu starten', 'Restart VeloxScreen'),
    ('Konfiguration', 'Configuration'),
    ('Verbinde ...', 'Connecting ...'),
    ('Verbindung gestört', 'Connection lost'),
    ('Moonraker verbunden', 'Moonraker connected'),
    ('sichtbar', 'visible'),
    ('erkannt', 'detected'),
    ('ausgeblendet', 'hidden'),
    ('Warte auf Daten', 'Waiting for data'),
    ('Heizbett', 'Heated bed'),
    ('Soll: ', 'Target: '),
    ('Soll ', 'Target '),
    ('Nur Sensor · keine Steuerung', 'Sensor only · no control'),
    ('Temperaturgeregelter Lüfter', 'Temperature-controlled fan'),
    ('Temp.-Lüfter', 'Temperature fan'),
    ('Nur Anzeige', 'Read-only'),
    ('Sensor', 'Sensor'),
    ('Temperatur setzen', 'Set temperature'),
    ('Solltemperatur · ', 'Target temperature · '),
    ('Firmware Retract', 'Firmware Retract'),
    ('Hotend nur ', 'Hotend only '),
    (' nötig mindestens ', ' minimum required '),
    ('Rückzug', 'Retraction'),
    ('Bewegung während Druck/Pause gesperrt.', 'Movement is disabled while printing/paused.'),
    ('Achse ist nicht referenziert.', 'Axis is not homed.'),
    ('Achsen', 'Axes'),
    ('Setzen', 'Set'),
    ('noch nicht gesetzt', 'pending'),
    ('Mesh-Messung', 'Bed mesh calibration'),
    ('Mesh löschen', 'Clear bed mesh'),
    ('Profil: ', 'Profile: '),
    ('Minimum:', 'Minimum:'),
    ('Maximum:', 'Maximum:'),
    ('Messpunkte', 'measured points'),
    ('Kein aktives Bed Mesh mit Messwerten.', 'No active bed mesh with measured values.'),
    ('Noch keine Mesh-Daten', 'No mesh data yet'),
    ('Druck starten:', 'Start print:'),
    ('Druck', 'Print'),
    ('Kein Druck', 'No print'),
    ('Unbekannt', 'Unknown'),
    ('ausführen?', 'execute?'),
    ('Homing', 'Homing'),
    ('ALLE', 'ALL'),
    ('LED-Effekte', 'LED Effects'),
    ('FEHLER:', 'ERROR:'),
]
# Translate longer phrases first, so fragments do not break complete sentences.
_I18N.sort(key=lambda p: len(p[0]), reverse=True)

_ADDED_EN_DE = {
    ('Temperature presets', 'Temperatur-Presets'),
    ('Edit presets', 'Presets bearbeiten'),
    ('Preset name', 'Preset-Name'),
    ('Edit temperature presets', 'Temperatur-Presets bearbeiten'),
    ('Presets', 'Presets'),
    ('New preset', 'Neues Preset'),
    ('Delete preset', 'Preset löschen'),
    ('Save preset', 'Preset speichern'),
    ('Close', 'Schließen'),
    ('Name', 'Name'),
    ('No preset selected', 'Kein Preset gewählt'),
    ('Only checked heaters are changed when applying a preset.', 'Nur aktivierte Heizungen werden beim Preset geändert.'),
    ('Select at least one heater.', 'Mindestens eine Heizung auswählen.'),
    ('Invalid preset name.', 'Ungültiger Preset-Name.'),
    ('A preset with that name already exists.', 'Ein Preset mit diesem Namen existiert bereits.'),
    ('Could not save config: ', 'Konfig konnte nicht gespeichert werden: '),
    ('Maximum speed', 'Maximale Geschwindigkeit'),
    ('Maximum acceleration', 'Maximale Beschleunigung'),
    ('Max speed', 'Max Speed'),
    ('Max accel', 'Max Beschleunigung'),
    ('Language / Sprache', 'Sprache / Language'),
    ('Apply language', 'Sprache übernehmen'),
    ('Default: English. Changing the language restarts the UI.', 'Standard: Englisch. Ein Sprachwechsel startet die Oberfläche neu.'),
    ('Tap any cell to inspect its measured Z value.', 'Zelle antippen, um den gemessenen Z-Wert anzuzeigen.'),
    ('Blue = lower · White = near zero · Red = higher', 'Blau = tiefer · Weiß = nahe Null · Rot = höher'),
    ('No active bed mesh', 'Kein aktives Bed Mesh'),
    ('No active bed mesh with measured values.', 'Kein aktives Bed Mesh mit Messpunkten.'),
    ('No mesh measurements available', 'Keine Bed-Mesh-Messdaten vorhanden'),
    ('No temperature presets. Tap Edit presets.', 'Keine Temperatur-Presets. „Presets bearbeiten“ wählen.'),
    ('Hotend', 'Hotend'),
    ('Bed', 'Bett'),
    ('Chamber', 'Kammer'),
    (' target', ' Zieltemperatur'),
    ('Tap any cell', 'Zelle antippen'),
    ('Space', 'Leerzeichen'),
    ('Backspace', 'Rücktaste'),
    ('Clear', 'Löschen'),
    ('Cancel', 'Abbrechen'),
    ('Save', 'Speichern'),
    ('Settings', 'Einstellungen'),
    ('LED Effects', 'LED-Effekte'),
    ('Activate', 'Aktivieren'),
    ('Replace', 'Ersetzen'),
    ('Stop all effects', 'Alle Effekte stoppen'),
    ('Stop', 'Stoppen'),
    ('Active', 'Aktiv'),
    ('Inactive', 'Inaktiv'),
    ('Fade duration', 'Überblendzeit'),
    ('Keyboard', 'Tastatur'),
    ('G-Code keyboard', 'G-Code-Tastatur'),
    ('Enter G-Code or macro command', 'G-Code oder Makrobefehl eingeben'),
    ('Tap to enter G-Code / macro', 'Tippen: G-Code / Makro eingeben'),
    ('Use command', 'Befehl übernehmen'),
    ('Send', 'Senden'),
    ('Motion limits', 'Bewegungslimits'),
    ('Waiting for Klipper settings. Please try again.', 'Warte auf Klipper-Einstellungen. Bitte erneut versuchen.'),
    ('Hotend presets use the currently selected tool; bed and chamber are independent.', 'Hotend-Presets gelten für das gewählte Tool; Bett und Kammer sind unabhängig.'),
    ('Appearance / theme', 'Aussehen / Design'),
    ('Theme colors', 'Designfarben'),
    ('Theme colors · touch RGB editor', 'Designfarben · RGB-Touch-Editor'),
    ('Accent', 'Akzentfarbe'),
    ('Cards / overlays', 'Karten / Overlays'),
    ('Background', 'Hintergrund'),
    ('Sidebar', 'Seitenleiste'),
    ('Buttons', 'Schaltflächen'),
    ('Quick colors', 'Schnellfarben'),
    ('Edit theme colors', 'Designfarben bearbeiten'),
    ('Save theme', 'Design speichern'),
    ('Defaults', 'Standardfarben'),
    ('Choose any RGB color for accents, panels, backgrounds, sidebar and buttons.', 'RGB-Farben für Akzente, Karten, Hintergrund, Seitenleiste und Schaltflächen einstellen.'),
    ('Could not save theme: ', 'Design konnte nicht gespeichert werden: '),
    ('Startup screen', 'Startbildschirm'),
    ('Show startup screen', 'Startbildschirm anzeigen'),
    ('Speed-line animation', 'Speed-Linien animieren'),
    ('Startup duration', 'Startdauer'),
    ('Preview splash', 'Startanimation testen'),
    ('Save startup settings', 'Starteinstellungen speichern'),
    ('Startup preferences saved. Changes take effect at the next launch.',
     'Starteinstellungen gespeichert. Änderungen gelten beim nächsten Start.'),
    ('The logo transitions smoothly to the dashboard. Tap to skip.',
     'Das Logo geht weich ins Dashboard über. Tippen zum Überspringen.'),
    ('Choose a 1, 2, 3 or 5 second splash. Default is 3 seconds.',
     'Startbild mit 1, 2, 3 oder 5 Sekunden. Standard sind 3 Sekunden.'),
    ('Visibility not saved: ', 'Sichtbarkeit nicht gespeichert: '),
    ('Hide All', 'Alle ausblenden'),
    ('Show All', 'Alle anzeigen'),
    ('Search macros...', 'Makros suchen...'),
    ('Clear search', 'Suche löschen'),
    ('Select only the macros you want to see. Newly added macros stay hidden after Hide All.',
     'Wähle nur benötigte Makros. Neue Makros bleiben nach „Alle ausblenden“ verborgen.'),
    ('Show/hide a macro by tapping its checkbox. Hiding never deletes it from Klipper.',
     'Mit dem Kästchen ein- und ausblenden. Das Makro bleibt in Klipper erhalten.'),
    ('Macro visibility', 'Makro-Sichtbarkeit'),
    ('G-Code notifications', 'G-Code-Benachrichtigungen'),
    ('Show RESPOND / macro messages', 'RESPOND- / Makromeldungen anzeigen'),
    ('Show Klipper RESPOND output as a non-blocking popup, including IDEX toolchanges. Macros must emit RESPOND to provide a message.',
     'Klipper-RESPOND als kleines Popup anzeigen, auch bei IDEX-Toolwechseln. Das Makro muss dazu RESPOND ausgeben.'),
    ('Popup duration', 'Anzeigedauer'),
    ('Test popup', 'Popup testen'),
    ('Speed override (M220)', 'Geschwindigkeitsfaktor (M220)'),
    ('Flow override (M221)', 'Flussfaktor (M221)'),
    ('Speed / Flow Overrides', 'Geschwindigkeit / Flussfaktor'),
    ('Tap percentage to enter exact value', 'Prozentwert antippen für genaue Eingabe'),
    ('Flow override is global for the G-Code stream, including tool changes.',
     'Flussfaktor gilt global für den G-Code, auch bei Toolwechseln.'),
    ('Waiting for Klipper values', 'Warte auf Klipper-Werte'),
    ('Speed override', 'Geschwindigkeitsfaktor'),
    ('Flow override', 'Flussfaktor'),
    ('Motors Off', 'Motoren aus'),
    ('Disables all stepper motors. Rehome axes before moving again.',
     'Schaltet alle Schrittmotoren ab. Vor der nächsten Bewegung Achsen neu homen.'),
    ('Motors can only be disabled when no print is active.',
     'Motoren können nur ohne aktiven oder pausierten Druck abgeschaltet werden.'),
}


def tr(message):
    text = str(message)
    if LANGUAGE == 'de':
        for english, german in sorted(_ADDED_EN_DE, key=lambda pair: len(pair[0]), reverse=True):
            text = text.replace(english, german)
        return text
    for german, english in _I18N:
        text = text.replace(german, english)
    return text


def warn(parent, title, message):
    return QMessageBox.warning(parent, tr(title), tr(message))


def inform(parent, title, message):
    return QMessageBox.information(parent, tr(title), tr(message))

STYLE = '''
QWidget { background: #0c1119; color: #e8edf6; font-family: 'DejaVu Sans'; }
QLabel#title { font-size: 18px; font-weight: 750; }
QLabel#brand { font-size: 20px; font-weight: 800; color: #f2f7ff; }
QLabel#tempValue { font-size: 17px; font-weight: 700; color: #ffffff; }
QLabel#tempName { color: #b6cbe3; font-size: 12px; font-weight: 600; }
QLabel#subtitle { font-size: 9px; font-weight: 500; color: #8fa1b9; }
QLabel#metric { font-size: 23px; font-weight: 750; color: #fbfcff; }
QLabel#caption { color: #9eadc2; font-size: 11px; }
QFrame#card { background: #171f2d; border: 1px solid #263247; border-radius: 9px; }
QPushButton { background: #26364e; border: 1px solid #354966; border-radius: 8px; padding: 6px 9px; font-weight: 600; font-size: 12px; }
QPushButton:hover { background: #344b6e; }
QPushButton:pressed { background: #5179ad; }
QPushButton:disabled { color: #65738a; background: #1a2534; }
QPushButton#primary { background: #2866bd; border: 1px solid #3377d8; }
QPushButton#nav { background: #151f2d; text-align: left; border: 0; padding: 8px 6px; font-size: 12px; }
QPushButton#nav:checked { background: #244c79; border-left: 4px solid #75c3ff; }
QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit { background: #111a27; border: 1px solid #3a4c67; border-radius: 7px; padding: 7px; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: #152031; width: 7px; }
QScrollBar::handle:vertical { background: #50627d; border-radius: 3px; }
'''


# Theme colors are per-printer settings, never Klipper settings.
# RGB color editor supports any 24-bit color without an OS keyboard.
THEME_DEFAULTS = {
    'accent_color': '#2866bd',
    'overlay_color': '#171f2d',
    'background_color': '#0c1119',
    'sidebar_color': '#111925',
    'button_color': '#26364e',
}
THEME_LABELS = {
    'accent_color': 'Accent',
    'overlay_color': 'Cards / overlays',
    'background_color': 'Background',
    'sidebar_color': 'Sidebar',
    'button_color': 'Buttons',
}


def checked_hex(value, fallback):
    value = str(value).strip()
    if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return value.lower()
    return fallback


def load_theme():
    return {key: checked_hex(conf('appearance', key, default), default)
            for key, default in THEME_DEFAULTS.items()}


def rgb_mix(first, second, weight):
    a, b = QColor(first), QColor(second)
    weight = max(0.0, min(1.0, weight))
    return QColor(round(a.red() * (1 - weight) + b.red() * weight),
                  round(a.green() * (1 - weight) + b.green() * weight),
                  round(a.blue() * (1 - weight) + b.blue() * weight)).name()


def text_on(color):
    c = QColor(color)
    # Relative luminance for automatic contrast on light/dark theme colors.
    channels = [n / 255 for n in (c.red(), c.green(), c.blue())]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
              for x in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return '#17202d' if luminance > 0.28 else '#f2f6ff'


STYLE_BASE = STYLE
THEME = load_theme()


def themed_style(theme):
    bg = theme['background_color']
    panel = theme['overlay_color']
    side = theme['sidebar_color']
    accent = theme['accent_color']
    btn = theme['button_color']
    bg_ink, panel_ink, side_ink, btn_ink, accent_ink = map(
        text_on, (bg, panel, side, btn, accent))
    replacements = {
        '#0c1119': bg,
        '#e8edf6': bg_ink,
        '#f2f7ff': side_ink,
        '#ffffff': panel_ink,
        '#b6cbe3': rgb_mix(panel_ink, panel, 0.20),
        '#8fa1b9': rgb_mix(side_ink, side, 0.36),
        '#fbfcff': panel_ink,
        '#9eadc2': rgb_mix(bg_ink, bg, 0.34),
        '#171f2d': panel,
        '#263247': rgb_mix(panel, panel_ink, 0.17),
        '#26364e': btn,
        '#354966': rgb_mix(btn, btn_ink, 0.22),
        '#344b6e': rgb_mix(btn, btn_ink, 0.16),
        '#5179ad': rgb_mix(btn, accent, 0.56),
        '#65738a': rgb_mix(bg_ink, bg, 0.52),
        '#1a2534': rgb_mix(btn, bg, 0.42),
        '#2866bd': accent,
        '#3377d8': rgb_mix(accent, accent_ink, 0.26),
        '#151f2d': rgb_mix(side, bg, 0.22),
        '#244c79': rgb_mix(side, accent, 0.58),
        '#75c3ff': rgb_mix(accent, accent_ink, 0.45),
        '#111a27': rgb_mix(bg, panel, 0.58),
        '#3a4c67': rgb_mix(panel, panel_ink, 0.30),
        '#152031': rgb_mix(bg, panel, 0.6),
        '#50627d': rgb_mix(panel, bg_ink, 0.36),
    }
    # Substitutions happen simultaneously, so user-chosen colors never get
    # accidentally replaced again when they match a default hex color.
    style = re.sub(r'#[0-9a-fA-F]{6}',
                   lambda m: replacements.get(m.group().lower(), m.group()),
                   STYLE_BASE)
    style += f"\nQFrame#veloxSidebar {{ background: {side}; }}"
    style += f"\nQFrame#card QLabel {{ color: {panel_ink}; }}"
    style += f"\nQFrame#card QLabel#caption {{ color: {rgb_mix(panel_ink, panel, 0.32)}; }}"
    style += f"\nQFrame#card QLabel#tempName {{ color: {rgb_mix(panel_ink, panel, 0.22)}; }}"
    style += f"\nQFrame#veloxSidebar QLabel {{ color: {side_ink}; }}"
    style += f"\nQFrame#veloxSidebar QLabel#subtitle {{ color: {rgb_mix(side_ink, side, 0.38)}; }}"
    style += f"\nQPushButton {{ color: {btn_ink}; }}"
    style += f"\nQPushButton#primary {{ color: {accent_ink}; }}"
    style += f"\nQPushButton#nav {{ color: {side_ink}; background: {rgb_mix(side, bg, 0.22)}; }}"
    style += f"\nQPushButton#nav:checked {{ color: {text_on(rgb_mix(side, accent, 0.58))}; }}"
    style += f"\nQComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{ color: {bg_ink}; }}"
    return style


def fan_slider_style(theme):
    return ('QSlider::groove:horizontal {{ height: 10px; background: {groove}; border-radius: 5px; }} '
            'QSlider::handle:horizontal {{ width: 26px; margin: -11px 0; background: {accent}; '
            'border-radius: 12px; }}').format(
                groove=rgb_mix(theme['overlay_color'], theme['accent_color'], 0.20),
                accent=theme['accent_color'])


STYLE = themed_style(THEME)


def display_profile(screen_width, screen_height, configured='auto'):
    """Use Qt logical screen size (not physical inch size) for layout.

    7-inch panels can still report 800x480; use an optional UI scale override.
    """
    width, height = max(1, int(screen_width)), max(1, int(screen_height))
    compact = width < 700 or height < 400
    tiny = width < 460 or height < 300
    if width >= 1024 and height >= 600:
        automatic_scale = 1.12
    elif tiny:
        automatic_scale = 0.85
    elif compact:
        automatic_scale = 0.92
    else:
        automatic_scale = 1.0
    try:
        scale = automatic_scale if str(configured).strip().lower() in ('auto', '') else float(configured)
    except (TypeError, ValueError, OverflowError):
        scale = automatic_scale
    scale = max(0.7, min(1.5, scale))
    return compact, tiny, scale


def scaled_style(style, factor):
    """Adjust stylesheet pixel font sizes and padding without changing geometry."""
    if abs(factor - 1.0) < 0.005:
        return style
    def replace(match):
        value = float(match.group(2))
        return match.group(1) + str(max(9, round(value * factor))) + 'px'
    return re.sub(r'(font-size:\s*)([0-9]+(?:\.[0-9]+)?)px', replace, style)


def fit_dialog(dialog, desired_width, desired_height):
    """Keep popups within the actual usable display; never force 740px offscreen."""
    screen = QApplication.primaryScreen()
    rect = screen.availableGeometry() if screen is not None else None
    width = max(240, rect.width() - 12) if rect is not None else desired_width
    height = max(180, rect.height() - 12) if rect is not None else desired_height
    dialog.resize(min(desired_width, width), min(desired_height, height))
    dialog.setMaximumSize(width, height)


def touch_dialog_style(source):
    screen = QApplication.primaryScreen()
    if screen is None:
        return source
    _, tiny, scale = display_profile(screen.geometry().width(), screen.geometry().height(),
                                     conf('display', 'ui_scale', 'auto'))
    if tiny:
        source += '\nQPushButton { min-height: 22px; padding: 1px 2px; } QLineEdit { min-height: 26px; }'
    return scaled_style(source, scale)


def label(text, name=None):
    l = QLabel(tr(text))
    if name:
        l.setObjectName(name)
    l.setWordWrap(True)
    return l


def button(text, func, primary=False):
    b = QPushButton(tr(text))
    if primary:
        b.setObjectName('primary')
    b.clicked.connect(func)
    b.setMinimumHeight(40)
    return b


def card(title=None):
    frame = QFrame()
    frame.setObjectName('card')
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(6)
    if title:
        lay.addWidget(label(title))
    return frame, lay


def row(*widgets):
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for w in widgets:
        if isinstance(w, QHBoxLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay




class FingerScrollArea(QScrollArea):
    """Touch-friendly kinetic scrolling for 800x480 DSI screens.

    The mouse gesture also handles panels whose touch driver sends emulated
    mouse events. Short taps still activate buttons; dragging scrolls the page.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        QScroller.grabGesture(self.viewport(), QScroller.LeftMouseButtonGesture)
        scroller = QScroller.scroller(self.viewport())
        props = scroller.scrollerProperties()
        props.setScrollMetric(QScrollerProperties.DragStartDistance, 0.004)
        props.setScrollMetric(QScrollerProperties.VerticalOvershootPolicy,
                              QScrollerProperties.OvershootAlwaysOff)
        props.setScrollMetric(QScrollerProperties.HorizontalOvershootPolicy,
                              QScrollerProperties.OvershootAlwaysOff)
        scroller.setScrollerProperties(props)


class TouchNumberDialog(QDialog):
    """Integrated numeric touch keypad. No system/desktop keyboard required."""
    def __init__(self, parent, title, value, minimum=0.0, maximum=300.0, decimals=2):
        super().__init__(parent)
        self.setWindowTitle(tr(title))
        self.setModal(True)
        self.setStyleSheet(touch_dialog_style(STYLE + "QPushButton { min-height: 45px; font-size: 19px; } QLineEdit { font-size: 24px; font-weight: bold; min-height: 42px; }"))
        self.low, self.high, self.decimals = minimum, maximum, decimals
        fit_dialog(self, 380, 398)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 10)
        lay.setSpacing(5)
        lay.addWidget(label(title))
        self.display = QLineEdit(f'{value:.{decimals}f}')
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignRight)
        self._first_key = True
        lay.addWidget(self.display)
        g = QGridLayout()
        g.setSpacing(5)
        for r, cells in enumerate((('7','8','9'),('4','5','6'),('1','2','3'),('±','0','.'))):
            for c, char in enumerate(cells):
                b = QPushButton(char)
                b.clicked.connect(partial(self.key, char))
                g.addWidget(b,r,c)
        lay.addLayout(g)
        actions=QHBoxLayout()
        for txt, key in [('Löschen','CLR'),('⌫','DEL')]:
            b=QPushButton(txt)
            b.clicked.connect(partial(self.key,key))
            actions.addWidget(b)
        lay.addLayout(actions)
        footer=QHBoxLayout()
        cancel=button('Abbrechen', self.reject)
        accept=button('Übernehmen', self.accept_number, True)
        footer.addWidget(cancel)
        footer.addWidget(accept)
        lay.addLayout(footer)

    def key(self, k):
        old=self.display.text()
        if self._first_key and k not in ('DEL', '±'):
            old = ''
        self._first_key = False
        if k=='CLR': new=''
        elif k=='DEL': new=old[:-1]
        elif k=='±': new=old[1:] if old.startswith('-') else '-'+old
        elif k=='.': new=old if '.' in old else (old or '0')+'.'
        else: new=(old+k)[:12]
        self.display.setText(new)

    def accept_number(self):
        try: n=float(self.display.text())
        except ValueError:
            warn(self,'Eingabe','Bitte eine Zahl eingeben.')
            return
        if not self.low <= n <= self.high:
            warn(self,'Bereich',f'Erlaubt: {self.low:g} bis {self.high:g}')
            return
        self.result_value=round(n,self.decimals)
        self.accept()

    @classmethod
    def get_value(cls,parent,title,value,minimum=0,maximum=300,decimals=2):
        dialog=cls(parent,title,value,minimum,maximum,decimals)
        if dialog.exec_()==QDialog.Accepted:
            return dialog.result_value,True
        return value,False


class TouchSpinBox(QDoubleSpinBox):
    """Tap the value to open a large numeric keypad; no onscreen OS keyboard."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setReadOnly(True)
        self.setMinimumHeight(42)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self,obj,event):
        if obj is self.lineEdit() and event.type()==event.MouseButtonRelease:
            self.show_pad()
            return True
        return super().eventFilter(obj,event)

    def mouseReleaseEvent(self,event):
        self.show_pad()
        event.accept()

    def show_pad(self):
        number,ok=TouchNumberDialog.get_value(self.window(),self.property('touch_title') or 'Wert eingeben',self.value(),self.minimum(),self.maximum(),self.decimals())
        if ok:
            self.setValue(number)
            self.setProperty('velox_dirty', True)


def parse_mesh(raw):
    """Return only a complete finite numeric grid, never guess missing measurements."""
    import math
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    rows = []
    width = None
    for row_ in raw:
        if not isinstance(row_, (list, tuple)) or not row_:
            return []
        try:
            values = [float(v) for v in row_]
        except (ValueError, TypeError):
            return []
        if not all(math.isfinite(v) for v in values):
            return []
        if width is None:
            width = len(values)
        if width != len(values):
            return []
        rows.append(values)
    return rows


class ClickableMetric(QLabel):
    clicked = pyqtSignal()
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class MeshHeatmap(QWidget):
    """Colored measured Bed Mesh grid: each cell is one probed point.

    No smoothing or interpolation is used. Matrix has Y-min first per Klipper.
    """
    selected = pyqtSignal(int, int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.matrix = []
        self.setMinimumHeight(245)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_matrix(self, matrix):
        if matrix != self.matrix:
            self.matrix = matrix
            self.update()

    def _geometry(self):
        if not self.matrix:
            return None
        rows = len(self.matrix)
        columns = len(self.matrix[0])
        # Slight outer margin to prevent anti-aliased clipping.
        rect = self.rect().adjusted(2, 2, -2, -2)
        return rect, rows, columns, rect.width()/columns, rect.height()/rows

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#121a29'))
        g = self._geometry()
        if g is None:
            painter.setPen(QColor('#9baec5'))
            painter.drawText(self.rect(), Qt.AlignCenter, tr('No mesh measurements available'))
            return
        rect, rows, columns, cw, ch = g
        vals = [v for r in self.matrix for v in r]
        negative = max(-min(vals), 0.000001)
        positive = max(max(vals), 0.000001)
        for screen_row in range(rows):
            # Top of the picture represents maximum Y.
            y_idx = rows - screen_row - 1
            for x_idx in range(columns):
                value = self.matrix[y_idx][x_idx]
                intensity = min(1., abs(value) / (positive if value >= 0 else negative))
                if value > 0:
                    color = QColor.fromRgbF(0.95, 0.92*(1-intensity)+0.06*intensity,
                                             0.92*(1-intensity)+0.08*intensity)
                else:
                    color = QColor.fromRgbF(0.90*(1-intensity)+0.12*intensity,
                                             0.92*(1-intensity)+0.16*intensity, 0.98)
                x = rect.left() + round(x_idx*cw)
                y = rect.top() + round(screen_row*ch)
                w = round((x_idx+1)*cw) - round(x_idx*cw)
                h = round((screen_row+1)*ch) - round(screen_row*ch)
                cell = QRect(x, y, w, h)
                painter.fillRect(cell, color)
                painter.setPen(QPen(QColor(38, 49, 70, 180), 1))
                painter.drawRect(cell.adjusted(0, 0, -1, -1))
                if min(w, h) >= 26:
                    font = painter.font()
                    font.setPixelSize(max(9, min(14, int(min(w/4.5, h/3.2)))))
                    painter.setFont(font)
                    painter.setPen(QColor('#14192d'))
                    painter.drawText(cell, Qt.AlignCenter, f'{value:+.2f}' if value < 0 else f'{value:.2f}')
        painter.end()

    def mouseReleaseEvent(self, event):
        g = self._geometry()
        if g is not None:
            rect, rows, columns, cw, ch = g
            if rect.contains(event.pos()):
                col = max(0, min(columns-1, int((event.x()-rect.left())/cw)))
                screen_row = max(0, min(rows-1, int((event.y()-rect.top())/ch)))
                row_idx = rows - screen_row - 1
                self.selected.emit(col, row_idx, self.matrix[row_idx][col])
        event.accept()


class TouchTextDialog(QDialog):
    """Simple standalone alphanumeric keypad for editing preset names on tty/X11."""
    def __init__(self, parent, value):
        super().__init__(parent)
        self.setWindowTitle(tr('Preset name'))
        self.setStyleSheet(touch_dialog_style(STYLE + 'QPushButton { min-height: 36px; font-size: 15px; padding: 3px; }'))
        fit_dialog(self, 700, 360)
        layout = QVBoxLayout(self)
        self.name_edit = QLineEdit(value)
        self.name_edit.setReadOnly(True)
        self.name_edit.setMinimumHeight(40)
        layout.addWidget(self.name_edit)
        for keys in ('1234567890', 'QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM_-'):
            r = QHBoxLayout()
            for key in keys:
                b = QPushButton(key)
                b.clicked.connect(partial(self.key, key))
                r.addWidget(b)
            layout.addLayout(r)
        controls = QHBoxLayout()
        for text, character in [('Space', ' '), ('Backspace', '<'), ('Clear', '!')]:
            b = button(text, partial(self.key, character))
            controls.addWidget(b)
        layout.addLayout(controls)
        layout.addLayout(row(button('Cancel', self.reject), button('Save', self.accept, True)))

    def key(self, symbol):
        value = self.name_edit.text()
        if symbol == '<':
            value = value[:-1]
        elif symbol == '!':
            value = ''
        else:
            value = (value + symbol)[:48]
        self.name_edit.setText(value)

    @classmethod
    def get_text(cls, parent, value):
        dialog = cls(parent, value)
        if dialog.exec_() == QDialog.Accepted:
            return dialog.name_edit.text().strip(), True
        return value, False


class TouchCommandDialog(QDialog):
    """Touch-first ASCII keyboard for console commands on 800x480 DSI screens."""
    def __init__(self, parent, text=''):
        super().__init__(parent)
        self.setWindowTitle(tr('G-Code keyboard'))
        self.setModal(True)
        fit_dialog(self, 700, 390)
        self.setStyleSheet(touch_dialog_style(STYLE + 'QPushButton { min-height: 36px; font-size: 16px; padding: 1px; } '
                           'QLineEdit { min-height: 42px; font-size: 19px; }'))
        self.upper = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(5)
        self.entry = QLineEdit(text)
        self.entry.setMaxLength(500)
        # Read-only prevents missing hardware/onscreen keyboard interaction;
        # cursor positioning still works and the custom keys edit text.
        self.entry.setReadOnly(True)
        self.entry.setPlaceholderText(tr('Enter G-Code or macro command'))
        lay.addWidget(self.entry)
        self.alpha_keys = []
        for chars in ('1234567890', 'QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM_-=', './:+,()[]{}', '!@#$%^&*?;~\\|'):
            line = QHBoxLayout()
            line.setSpacing(3)
            for char in chars:
                b = QPushButton(char)
                b.clicked.connect(partial(self.press_key, char))
                line.addWidget(b, 1)
                if char.isalpha(): self.alpha_keys.append((char,b))
            lay.addLayout(line)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        for title, symbol in [('Shift', 'SHIFT'), ('Space', ' '), ('Quote', '"'),
                              ('Backspace','BACK'), ('Clear', 'CLEAR')]:
            b = button(title, partial(self.press_key, symbol))
            controls.addWidget(b)
        lay.addLayout(controls)
        lay.addLayout(row(button('Cancel', self.reject), button('Use command', self.accept, True)))

    def press_key(self, symbol):
        if symbol == 'SHIFT':
            self.upper = not self.upper
            for key,b in self.alpha_keys:
                b.setText(key if self.upper else key.lower())
            return
        text = self.entry.text()
        caret = self.entry.cursorPosition()
        if symbol == 'CLEAR':
            self.entry.clear()
            return
        if symbol == 'BACK':
            if caret:
                self.entry.setText(text[:caret-1] + text[caret:])
                self.entry.setCursorPosition(caret-1)
            return
        if not self.upper and symbol.isalpha():
            symbol = symbol.lower()
        if len(text) < 500:
            self.entry.setText(text[:caret] + symbol + text[caret:])
            self.entry.setCursorPosition(caret+len(symbol))

    @classmethod
    def get_command(cls, parent, text=''):
        dialog = cls(parent, text)
        if dialog.exec_() == QDialog.Accepted:
            return dialog.entry.text(), True
        return text, False


class TouchCommandInput(QLineEdit):
    """Tap a console field to open VeloxScreen's integrated touch keyboard."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumHeight(43)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_keyboard()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def open_keyboard(self):
        value, ok = TouchCommandDialog.get_command(self.window(), self.text())
        if ok:
            self.setText(value)


class TouchCheckBox(QCheckBox):
    """Large, clear outline and checkmark for 4.3/5-inch touch displays.

    Uses the current VeloxScreen theme. The entire row remains clickable and
    QCheckBox's native checked/toggled signals continue to work normally.
    """
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(43)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        theme = getattr(self.window(), 'theme', THEME)
        accent = QColor(theme.get('accent_color', '#2866bd'))
        surface = theme.get('overlay_color', '#171f2d')
        foreground = QColor(text_on(surface))
        edge = QColor(rgb_mix(surface, '#ffffff', 0.45))
        if not self.isEnabled():
            foreground.setAlpha(110)
            edge.setAlpha(105)
            accent.setAlpha(110)
        side = 24
        left = 4
        top = (self.height() - side) // 2
        rect = QRect(left, top, side, side)
        p.setPen(QPen(accent if self.isChecked() else edge, 2))
        p.setBrush(accent if self.isChecked() else QColor(rgb_mix(surface, '#000000', 0.18)))
        p.drawRoundedRect(rect, 4, 4)
        if self.isChecked():
            tick = QColor(text_on(accent.name()))
            tick.setAlpha(255 if self.isEnabled() else 130)
            p.setPen(QPen(tick, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(left + 5, top + 12, left + 10, top + 17)
            p.drawLine(left + 10, top + 17, left + 19, top + 7)
        p.setPen(foreground)
        p.setFont(self.font())
        p.drawText(QRect(left + side + 12, 0,
                         max(0, self.width() - left - side - 12), self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.end()


class TemperaturePresetEditor(QDialog):
    """Edits arbitrary number of presets in ~/printer_data/config/veloxscreen.conf.

    Each preset may target only the desired heaters; there is no fixed four-item cap.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr('Edit temperature presets'))
        self.setStyleSheet(touch_dialog_style(STYLE))
        fit_dialog(self, 740, 430)
        self._names = []
        self._original = None
        main = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(label('Presets'))
        self.selector = QComboBox()
        self.selector.setMinimumHeight(45)
        self.selector.currentTextChanged.connect(self.load_preset)
        left.addWidget(self.selector)
        left.addWidget(button('New preset', self.new_preset, True))
        left.addWidget(button('Delete preset', self.delete_preset))
        left.addStretch()
        main.addLayout(left, 1)
        right = QVBoxLayout()
        self.name_button = button('Name', self.rename_preset)
        right.addWidget(self.name_button)
        self.controls = {}
        for label_text, key, maximum in [('Hotend', 'nozzle', 450),
                                         ('Bed', 'bed', 160),
                                         ('Chamber', 'chamber', 150)]:
            line = QHBoxLayout()
            check = TouchCheckBox(tr(label_text))
            check.setMinimumHeight(40)
            spin = TouchSpinBox()
            spin.setProperty('touch_title', tr(label_text + ' target'))
            spin.setRange(0, maximum)
            spin.setDecimals(0)
            spin.setFixedWidth(90)
            line.addWidget(check)
            line.addWidget(spin)
            right.addLayout(line)
            self.controls[key] = (check, spin)
        right.addWidget(label('Only checked heaters are changed when applying a preset.', 'caption'))
        right.addStretch()
        right.addLayout(row(button('Close', self.accept), button('Save preset', self.save_preset, True)))
        main.addLayout(right, 2)
        self.update_names()

    def update_names(self, select=None):
        names = list(config.options('temperature_presets')) if config.has_section('temperature_presets') else []
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(names)
        if select in names:
            self.selector.setCurrentIndex(names.index(select))
        self.selector.blockSignals(False)
        self.load_preset()

    def load_preset(self, ignored=None):
        name = self.selector.currentText()
        self._original = name
        self.name_button.setText(tr('Name') + ': ' + name if name else tr('No preset selected'))
        value = config.get('temperature_presets', name, fallback='') if name else ''
        targets = {}
        for item in value.split(','):
            if ':' not in item: continue
            key, raw = item.split(':', 1)
            try: targets[key.strip().lower()] = float(raw.strip())
            except ValueError: pass
        for key, (check, spin) in self.controls.items():
            check.setChecked(key in targets)
            spin.setValue(targets.get(key, 0.0))

    def save_preset(self):
        name = self.selector.currentText()
        if not name: return
        values = []
        for key, (check, spin) in self.controls.items():
            if check.isChecked():
                values.append(f'{key}:{spin.value():g}')
        if not values:
            warn(self, 'Presets', 'Select at least one heater.')
            return
        config.set('temperature_presets', name, ', '.join(values))
        try:
            save_config()
        except OSError as exc:
            warn(self, 'Presets', 'Could not save config: ' + str(exc))

    def new_preset(self):
        if not config.has_section('temperature_presets'):
            config.add_section('temperature_presets')
        i = 1
        while config.has_option('temperature_presets', f'Preset {i}'):
            i += 1
        new = f'Preset {i}'
        config.set('temperature_presets', new, 'nozzle:200')
        try:
            save_config()
            self.update_names(new)
        except OSError as exc:
            warn(self, 'Presets', 'Could not save config: ' + str(exc))

    def rename_preset(self):
        old = self.selector.currentText()
        if not old: return
        new, ok = TouchTextDialog.get_text(self, old)
        if not ok or new == old: return
        if not new or new.startswith(('#', ';', '[')) or '=' in new or '\n' in new or '\r' in new:
            warn(self, 'Presets', 'Invalid preset name.')
            return
        if config.has_option('temperature_presets', new):
            warn(self, 'Presets', 'A preset with that name already exists.')
            return
        value = config.get('temperature_presets', old)
        config.remove_option('temperature_presets', old)
        config.set('temperature_presets', new, value)
        try:
            save_config()
            self.update_names(new)
        except OSError as exc:
            warn(self, 'Presets', 'Could not save config: ' + str(exc))

    def delete_preset(self):
        name = self.selector.currentText()
        if name:
            config.remove_option('temperature_presets', name)
            try:
                save_config()
                self.update_names()
            except OSError as exc:
                warn(self, 'Presets', 'Could not save config: ' + str(exc))

class ThemeColorDialog(QDialog):
    """Touch-friendly 24-bit RGB theme editor for 800x480 DSI displays."""
    SWATCHES = ['#2866bd', '#1f9d8a', '#a855f7', '#ef4444',
                '#f59e0b', '#ec4899', '#e5e7eb', '#303d58']

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle(tr('Theme colors'))
        fit_dialog(self, 740, 428)
        self.setModal(True)
        self.setStyleSheet(STYLE)
        self.working = dict(owner.theme)
        self.active_role = 'accent_color'
        self.role_buttons = {}
        self.channels = []
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)
        root.addWidget(label('Theme colors · touch RGB editor'))
        middle = QHBoxLayout()
        middle.setSpacing(12)
        roles = QVBoxLayout()
        roles.setSpacing(4)
        for role, display in THEME_LABELS.items():
            b = QPushButton(tr(display))
            b.setMinimumHeight(40)
            b.setCheckable(True)
            b.clicked.connect(partial(self.choose_role, role))
            self.role_buttons[role] = b
            roles.addWidget(b)
        roles.addStretch(1)
        middle.addLayout(roles, 1)

        controls = QVBoxLayout()
        controls.setSpacing(6)
        self.hex_value = label('#2866bd')
        controls.addWidget(self.hex_value)
        for ix, name in enumerate(('R', 'G', 'B')):
            value_label = QLabel('0')
            value_label.setFixedWidth(40)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 255)
            slider.setSingleStep(1)
            slider.setMinimumHeight(36)
            slider.valueChanged.connect(self.channel_changed)
            r = QHBoxLayout()
            small = QLabel(name)
            small.setFixedWidth(24)
            r.addWidget(small)
            r.addWidget(slider, 1)
            r.addWidget(value_label)
            controls.addLayout(r)
            self.channels.append((slider, value_label))

        controls.addWidget(label('Quick colors', 'caption'))
        palette = QGridLayout()
        palette.setSpacing(5)
        for idx, color in enumerate(self.SWATCHES):
            swatch = QPushButton('')
            swatch.setMinimumHeight(29)
            swatch.setStyleSheet('QPushButton { background: %s; border: 1px solid #8998ae; border-radius: 6px; }' % color)
            swatch.clicked.connect(partial(self.set_color, color))
            palette.addWidget(swatch, idx // 4, idx % 4)
        controls.addLayout(palette)

        self.sample = QFrame()
        self.sample.setObjectName('themePreview')
        self.sample.setMinimumHeight(60)
        sample_layout = QHBoxLayout(self.sample)
        sample_layout.setContentsMargins(10, 6, 10, 6)
        self.sample_text = QLabel('VeloxScreen   240 °C')
        sample_layout.addWidget(self.sample_text)
        sample_layout.addStretch(1)
        self.sample_button = QLabel('APPLY')
        self.sample_button.setAlignment(Qt.AlignCenter)
        self.sample_button.setFixedSize(72, 32)
        sample_layout.addWidget(self.sample_button)
        controls.addWidget(self.sample)
        middle.addLayout(controls, 3)
        root.addLayout(middle, 1)

        footer = QHBoxLayout()
        footer.addWidget(button('Defaults', self.defaults))
        footer.addStretch(1)
        footer.addWidget(button('Cancel', self.reject))
        footer.addWidget(button('Save theme', self.save, True))
        root.addLayout(footer)
        self.choose_role(self.active_role)

    def choose_role(self, role):
        self.active_role = role
        for key, b in self.role_buttons.items():
            b.setChecked(key == role)
        col = QColor(self.working[role])
        self._loading = True
        for (slider, value_label), num in zip(self.channels,
                                              (col.red(), col.green(), col.blue())):
            slider.setValue(num)
            value_label.setText(str(num))
        self._loading = False
        self.update_sample()

    def channel_changed(self, _value):
        if self._loading:
            return
        numbers = [slide.value() for slide, _label in self.channels]
        for num, (_slide, value_label) in zip(numbers, self.channels):
            value_label.setText(str(num))
        self.working[self.active_role] = QColor(*numbers).name()
        self.update_sample()

    def set_color(self, color):
        self.working[self.active_role] = color
        self.choose_role(self.active_role)

    def defaults(self):
        self.working = dict(THEME_DEFAULTS)
        self.choose_role(self.active_role)

    def update_sample(self):
        color = self.working[self.active_role]
        self.hex_value.setText(tr(THEME_LABELS[self.active_role]) + '  ·  ' + color.upper())
        panel = self.working['overlay_color']
        accent = self.working['accent_color']
        self.sample.setStyleSheet('QFrame#themePreview { background: %s; border: 2px solid %s; border-radius: 8px; }' %
                                  (panel, rgb_mix(panel, accent, 0.35)))
        self.sample_text.setStyleSheet('color: %s; background: transparent; font-weight: bold;' % text_on(panel))
        self.sample_button.setStyleSheet('background: %s; color: %s; border-radius: 6px; font-weight: bold;' %
                                         (accent, text_on(accent)))

    def save(self):
        # Multiple keys are committed atomically in the existing config file.
        if not config.has_section('appearance'):
            config.add_section('appearance')
        original = {key: config.get('appearance', key, fallback=None)
                    for key in THEME_DEFAULTS}
        for key, color in self.working.items():
            config.set('appearance', key, checked_hex(color, THEME_DEFAULTS[key]))
        try:
            save_config()
        except OSError as exc:
            for key, value in original.items():
                if value is None:
                    config.remove_option('appearance', key)
                else:
                    config.set('appearance', key, value)
            warn(self, 'Settings', 'Could not save theme: ' + str(exc))
            return
        self.owner.apply_theme(self.working)
        self.accept()


# --- Native startup animation: no Chromium, video player or external runtime. ---
def startup_bool(key, default=True):
    value = conf('startup', key, 'true' if default else 'false').strip().lower()
    return value in ('1', 'on', 'yes', 'true')


def startup_image_path():
    configured = conf('startup', 'image', '').strip()
    if not configured:
        return BASE / 'assets' / 'veloxscreen_splash.png'
    path = Path(configured).expanduser()
    # Relative custom images are located beside veloxscreen.conf.
    return path if path.is_absolute() else CFG_PATH.parent / path


class StartupSplash(QWidget):
    """Full-window Qt overlay with animated speed streaks and an opacity crossfade.

    Rendering is done natively with QPainter and a QTimer, at about 30 fps.
    Both the image and the animation are optional; startup never blocks on an image.
    """
    def __init__(self, owner, image_path, duration_s=3, animate=True):
        super().__init__(owner)
        self.setObjectName('startupSplash')
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet('QWidget#startupSplash { background: transparent; }')
        self.duration_ms = max(1000, min(10000, int(duration_s * 1000)))
        self.animate = bool(animate)
        self._image = QPixmap(str(image_path))
        self._scaled = QPixmap()
        self._start_at = None
        self._fading = False
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.update)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade_anim = None
        self.setGeometry(owner.rect())
        self._resize_image()
        self.setCursor(Qt.PointingHandCursor)

    def _resize_image(self):
        if not self._image.isNull() and not self.size().isEmpty():
            self._scaled = self._image.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def resizeEvent(self, event):
        self._resize_image()
        super().resizeEvent(event)

    def start(self):
        self._start_at = time.monotonic()
        self._tick.start(33 if self.animate else 100)
        fade_ms = min(650, max(250, self.duration_ms // 4))
        QTimer.singleShot(self.duration_ms - fade_ms,
                          lambda: self.begin_fade(fade_ms))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._scaled.isNull():
            x = (self.width() - self._scaled.width()) // 2
            y = (self.height() - self._scaled.height()) // 2
            painter.drawPixmap(x, y, self._scaled)
        else:
            painter.fillRect(self.rect(), QColor('#07111d'))
            painter.setPen(QColor('#19d9f9'))
            painter.setFont(QFont('DejaVu Sans', 38, QFont.Bold))
            painter.drawText(self.rect(), Qt.AlignCenter, 'VeloxScreen')

        if self.animate and self._start_at is not None and not self._fading:
            elapsed = time.monotonic() - self._start_at
            width = max(1, self.width())
            height = self.height()
            # Staggered tracks above and below the wordmark, not over the logo text.
            tracks = [(0.19, 0.0, 115), (0.28, 0.32, 165),
                      (0.38, 0.65, 90), (0.77, 0.17, 135),
                      (0.84, 0.52, 105), (0.91, 0.85, 155)]
            for index, (rel_y, phase, length) in enumerate(tracks):
                position = ((elapsed * (0.57 + index * 0.045) + phase) % 1.55) - .27
                head = int(position * width)
                tail = head - int(length * width / 800.0)
                if head < 0 or tail > width:
                    continue
                y = int(rel_y * height)
                gradient = QLinearGradient(tail, y, head, y)
                gradient.setColorAt(0, QColor(0, 180, 255, 0))
                gradient.setColorAt(.60, QColor(10, 200, 255, 38))
                gradient.setColorAt(1, QColor(110, 248, 255, 220))
                painter.setPen(QPen(QBrush(gradient), 7, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(tail, y, head, y)
                painter.setPen(QPen(QBrush(gradient), 2, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(tail, y, head, y)
        painter.end()

    def mousePressEvent(self, event):
        # A tap skips the wait, but still uses a short smooth fade.
        self.begin_fade(240)
        event.accept()

    def begin_fade(self, milliseconds=650):
        if self._fading:
            return
        self._fading = True
        self._tick.stop()
        self._fade_anim = QPropertyAnimation(self._effect, b'opacity', self)
        self._fade_anim.setDuration(milliseconds)
        self._fade_anim.setStartValue(self._effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade_anim.finished.connect(self.finish)
        self._fade_anim.start()

    def finish(self):
        self._tick.stop()
        self.hide()
        if getattr(self.parent(), '_splash_overlay', None) is self:
            self.parent()._splash_overlay = None
        self.deleteLater()


class WifiSignals(QObject):
    scanned = pyqtSignal(object)
    connected = pyqtSignal(str)
    failed = pyqtSignal(str)


class Screen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('VeloxScreen')
        self.theme = dict(THEME)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        screen_width = available.width() if available is not None else 800
        screen_height = available.height() if available is not None else 480
        self.compact, self.tiny, self.ui_scale = display_profile(
            screen_width, screen_height, conf('display', 'ui_scale', 'auto'))
        self.setStyleSheet(scaled_style(STYLE, self.ui_scale))
        self.resize(screen_width, screen_height)
        self.s = {}
        self.all_objects = []
        self.network = Network(conf('connection', 'moonraker_url', 'http://127.0.0.1:7125'))
        self.network.data.connect(self.refresh)
        self.network.err.connect(self.error)
        self.network.reply.connect(self.response)
        self.network.gcode_messages.connect(self.handle_gcode_messages)
        self.busy_files = False
        self.selected_file = ''
        self.pages = {}
        self.nav_buttons = {}
        self.macro_names = []
        self.macro_commands = {}
        self._fan_pending = {}
        self._fan_timers = {}
        self._fan_last_sent = {}
        self.motor_off_buttons = []
        # M220/M221 G-code modifiers (not toolhead max limits).
        self.override_labels = {'speed': [], 'flow': []}
        self.override_buttons = {'speed': [], 'flow': []}
        self.override_seen = {}
        self.override_pending = {}
        self.override_bounds = {
            'speed': self._override_limits('speed', 10, 300),
            'flow': self._override_limits('flow', 50, 150),
        }
        self.led_widgets = {}
        self._last_led_signature = None
        self.hidden_macros = {x.strip().upper() for x in conf('macro_visibility','hidden','').split(',') if x.strip()}
        # 'all' honours the legacy hidden list. 'selected' is an allowlist:
        # new macros discovered after Hide All remain hidden by default.
        self.macro_visibility_mode = conf('macro_visibility', 'mode', 'all').strip().lower()
        if self.macro_visibility_mode not in ('all', 'selected'):
            self.macro_visibility_mode = 'all'
        self.selected_macros = {x.strip().upper() for x in
                                conf('macro_visibility', 'visible', '').split(',') if x.strip()}
        self.macro_check_widgets = {}
        self.confirm_enabled = conf('behavior','confirm_commands','false').strip().lower() in ('true','yes','1','on')
        self.notifications_enabled = conf('notifications', 'enabled', 'true').strip().lower() in ('true', 'yes', '1', 'on')
        self.notification_duration = int(max(2, min(10, fval(conf('notifications', 'duration_seconds', '4'), 4))))
        self._last_notification = ('', 0.0)
        self.wifi_signals = WifiSignals(self)
        self.wifi_signals.scanned.connect(self.wifi_scanned)
        self.wifi_signals.connected.connect(self.wifi_connected)
        self.wifi_signals.failed.connect(self.wifi_failed)
        self._wifi_busy = False
        self._wifi_info = None
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self.hide_notification)
        self._build()
        self._build_notification_banner()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.network.poll)
        self.timer.start(max(700, int(conf('connection', 'refresh_ms', '1200'))))
        QTimer.singleShot(100, self.network.poll)
        self.gcode_timer = QTimer(self)
        self.gcode_timer.timeout.connect(self.network.poll_gcode_store)
        self.gcode_timer.start(1000)
        QTimer.singleShot(250, self.network.poll_gcode_store)

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(142 if self.compact else 162)
        sidebar.setObjectName('veloxSidebar')
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(5, 7, 5, 7)
        brand = label('VeloxScreen', 'brand')
        brand.setWordWrap(False)
        sl.addWidget(brand)
        subtitle = label('TOUCH UI', 'subtitle')
        subtitle.setWordWrap(False)
        sl.addWidget(subtitle)
        self.connection = label('● Verbinde ...', 'caption')
        sl.addWidget(self.connection)
        sl.addSpacing(4)
        self.stack = QStackedWidget()
        sections = [('Dashboard', self.make_dashboard), ('Tools', self.make_tools),
                    ('Temperaturen', self.make_temperatures), ('Steuerung', self.make_control),
                    ('LED Effects', self.make_led_effects), ('Dateien', self.make_files), ('Kalibrierung', self.make_calib),
                    ('Konsole', self.make_console), ('Makros', self.make_macros),
                    ('Network', self.make_network), ('Einstellungen', self.make_settings)]
        self.compact_nav = None
        if self.compact:
            self.compact_nav = QComboBox()
            self.compact_nav.setMinimumHeight(30 if self.tiny else 38)
            self.compact_nav.addItems([tr(name) for name, _ in sections])
            self.compact_nav.currentIndexChanged.connect(
                lambda index: self.navigate(sections[index][0]) if index >= 0 else None)
            # Insert into the content column after its layout exists.
        for name, builder in sections:
            nav = QPushButton(tr(name))
            nav.setObjectName('nav')
            nav.setCheckable(True)
            nav.clicked.connect(partial(self.navigate, name))
            sl.addWidget(nav)
            self.nav_buttons[name] = nav
            self.pages[name] = self._page(name, builder)
            self.stack.addWidget(self.pages[name])
        sl.addStretch()
        sl.addWidget(label('v0.3.8 alpha', 'caption'))
        if self.compact:
            sidebar.hide()
        else:
            outer.addWidget(sidebar)
        content_side = QWidget()
        content_layout = QVBoxLayout(content_side)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        if self.compact_nav is not None:
            content_layout.addWidget(self.compact_nav)
        # No service controls in the header: settings owns the local restart action.
        # This also gives the 800x480 screens more usable height.
        content_layout.addWidget(self.stack, 1)
        outer.addWidget(content_side, 1)
        self.navigate('Dashboard')

    def _page(self, name, builder):
        scroll = FingerScrollArea()
        container = QWidget()
        content = QVBoxLayout(container)
        content.setContentsMargins(9, 7, 9, 9)
        content.setSpacing(7)
        content.addWidget(label(name, 'title'))
        builder(content)
        content.addStretch()
        scroll.setWidget(container)
        return scroll

    def navigate(self, name):
        self.stack.setCurrentWidget(self.pages[name])
        if self.compact_nav is not None:
            names = list(self.pages)
            self.compact_nav.blockSignals(True)
            self.compact_nav.setCurrentIndex(names.index(name))
            self.compact_nav.blockSignals(False)
        for n, b in self.nav_buttons.items():
            b.setChecked(n == name)
        if name == 'Dateien':
            self.network.load_files(None)
        if name == 'Network':
            self.scan_wifi()


    def show_startup_splash(self, force=False, duration=None, animated=None):
        if not force and not startup_bool('show_splash', True):
            return
        old = getattr(self, '_splash_overlay', None)
        if old is not None:
            old.finish()
        if duration is None:
            duration = fval(conf('startup', 'duration_seconds', '3'), 3.0)
        if animated is None:
            animated = startup_bool('animate_speedlines', True)
        self._splash_overlay = StartupSplash(
            self, startup_image_path(), duration_s=duration, animate=animated)
        self._splash_overlay.show()
        self._splash_overlay.raise_()
        self._splash_overlay.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        overlay = getattr(self, '_splash_overlay', None)
        if overlay is not None:
            overlay.setGeometry(self.rect())
        self._position_notification_banner()

    def preview_startup_splash(self):
        self.show_startup_splash(force=True,
                                 duration=self.startup_duration_combo.currentData(),
                                 animated=self.speed_lines_checkbox.isChecked())

    def save_startup_settings(self):
        values = {
            'show_splash': 'true' if self.show_splash_checkbox.isChecked() else 'false',
            'animate_speedlines': 'true' if self.speed_lines_checkbox.isChecked() else 'false',
            'duration_seconds': str(self.startup_duration_combo.currentData()),
        }
        if not config.has_section('startup'):
            config.add_section('startup')
        previous = {key: config.get('startup', key, fallback=None) for key in values}
        for key, value in values.items():
            config.set('startup', key, value)
        try:
            save_config()
        except OSError as exc:
            for key, value in previous.items():
                if value is None:
                    config.remove_option('startup', key)
                else:
                    config.set('startup', key, value)
            warn(self, 'Settings', 'Could not save config: ' + str(exc))
            return
        self.startup_status.setText(
            tr('Startup preferences saved. Changes take effect at the next launch.'))

    def motors_off(self):
        """Disable all motors (M84) while idle; do not interfere with a print."""
        stats = self.s.get('print_stats', {})
        if not isinstance(stats, dict) or not stats.get('state'):
            warn(self, 'Motors Off', 'Waiting for Klipper settings. Please try again.')
            return
        if stats.get('state') in ('printing', 'paused'):
            warn(self, 'Motors Off', 'Motors can only be disabled when no print is active.')
            return
        # Confirmation follows the user's Settings -> Confirmation dialogs toggle.
        self.confirm_script('M84', 'Motors Off')

    @staticmethod
    def _override_limits(which, default_min, default_max):
        minimum = int(max(1, min(1000, fval(conf('overrides', which + '_min', str(default_min)), default_min))))
        maximum = int(max(minimum, min(1000, fval(conf('overrides', which + '_max', str(default_max)), default_max))))
        return minimum, maximum

    def add_override_controls(self, parent_layout):
        """Add shared M220/M221 controls; both pages show the same live values."""
        columns = QVBoxLayout() if self.compact else QHBoxLayout()
        columns.setSpacing(6)
        for which, command, steps in (
            ('speed', 'M220', (-25, -5, 0, 5, 25)),
            ('flow', 'M221', (-5, -1, 0, 1, 5)),
        ):
            title = 'Speed override (M220)' if which == 'speed' else 'Flow override (M221)'
            frame, content = card(title)
            content.setContentsMargins(7, 7, 7, 8)
            content.setSpacing(4)
            value = ClickableMetric('-- %')
            value.setObjectName('metric')
            value.setCursor(Qt.PointingHandCursor)
            value.setToolTip(tr('Tap percentage to enter exact value'))
            value.clicked.connect(partial(self.set_override_exact, which))
            content.addWidget(value, 0, Qt.AlignCenter)
            self.override_labels[which].append(value)
            step_buttons = QHBoxLayout()
            step_buttons.setSpacing(3)
            for step in steps:
                caption = '100%' if step == 0 else f'{step:+d}'
                callback = partial(self.change_override, which, step)
                b = button(caption, callback, primary=(step == 0))
                b.setMinimumHeight(38)
                b.setMinimumWidth(0)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.setEnabled(False)
                step_buttons.addWidget(b, 1)
                self.override_buttons[which].append(b)
            content.addLayout(step_buttons)
            columns.addWidget(frame, 1)
        parent_layout.addLayout(columns)

    def _show_override(self, which, percent):
        for widget in self.override_labels[which]:
            widget.setText(f'{percent} %')
        for widget in self.override_buttons[which]:
            widget.setEnabled(True)

    def update_overrides(self, gcode_move):
        """Read Klipper status without overwriting rapid, unacknowledged taps."""
        for which, field in (('speed', 'speed_factor'), ('flow', 'extrude_factor')):
            raw = gcode_move.get(field) if isinstance(gcode_move, dict) else None
            if raw is None:
                continue
            try:
                measured = int(round(float(raw) * 100))
            except (TypeError, ValueError, OverflowError):
                continue
            self.override_seen[which] = measured
            pending = self.override_pending.get(which)
            if pending is not None:
                target, stamp = pending
                if measured == target or time.monotonic() - stamp > 5:
                    self.override_pending.pop(which, None)
                else:
                    self._show_override(which, target)
                    continue
            self._show_override(which, measured)

    def _submit_override(self, which, target):
        if which not in self.override_seen:
            warn(self, 'G-Code override', tr('Waiting for Klipper values'))
            return
        low, high = self.override_bounds[which]
        target = clamp_override(target, low, high)
        code = 'M220' if which == 'speed' else 'M221'
        if not self.confirm_script(f'{code} S{target}',
                                   'Speed override' if which == 'speed' else 'Flow override'):
            return
        self.override_pending[which] = (target, time.monotonic())
        self._show_override(which, target)

    def change_override(self, which, delta):
        pending = self.override_pending.get(which)
        baseline = pending[0] if pending is not None else self.override_seen.get(which)
        if baseline is None:
            return
        self._submit_override(which, 100 if delta == 0 else baseline + delta)

    def set_override_exact(self, which):
        pending = self.override_pending.get(which)
        baseline = pending[0] if pending is not None else self.override_seen.get(which)
        if baseline is None:
            return
        low, high = self.override_bounds[which]
        title = 'Speed override (%)' if which == 'speed' else 'Flow override (%)'
        value, ok = TouchNumberDialog.get_value(self, tr(title), baseline, low, high, 0)
        if ok:
            self._submit_override(which, value)

    def edit_motion_limit(self, which):
        toolhead = self.s.get('toolhead', {})
        printer = self.s.get('configfile', {}).get('settings', {}).get('printer', {})
        if which == 'velocity':
            value = fval(toolhead.get('max_velocity'), 300.0)
            configured = fval(printer.get('max_velocity'), max(300.0, value))
            title, command, units = 'Maximum speed', 'VELOCITY', 'mm/s'
        else:
            value = fval(toolhead.get('max_accel'), 5000.0)
            configured = fval(printer.get('max_accel'), max(5000.0, value))
            title, command, units = 'Maximum acceleration', 'ACCEL', 'mm/s²'
        if not toolhead or not printer:
            warn(self, 'Motion limits', 'Waiting for Klipper settings. Please try again.')
            return
        maximum = max(value, configured, 1.0)
        val, ok = TouchNumberDialog.get_value(self, title + ' (' + units + ')',
                                              value, 1.0, maximum, 0)
        if ok and val > 0:
            self.confirm_script(f'SET_VELOCITY_LIMIT {command}={val:g}', title)

    def rebuild_presets(self):
        entries = list(config.items('temperature_presets')) if config.has_section('temperature_presets') else []
        for grid, columns in ((getattr(self, 'dashboard_presets', None), 3),
                              (getattr(self, 'temp_preset_grid', None), 2)):
            if grid is None:
                continue
            self._clear_layout(grid)
            for i, (name, content) in enumerate(entries):
                b = button(name, partial(self.apply_temp_preset, name, content))
                b.setMinimumHeight(39)
                grid.addWidget(b, i // (1 if self.tiny else columns), i % (1 if self.tiny else columns))
            if not entries:
                grid.addWidget(label('No temperature presets. Tap Edit presets.', 'caption'), 0, 0)

    def edit_presets(self):
        editor = TemperaturePresetEditor(self)
        if editor.exec_() == QDialog.Accepted:
            self.rebuild_presets()

    def apply_language(self):
        lang = self.lang_combo.currentData()
        if lang == LANGUAGE:
            return
        try:
            set_config('appearance', 'language', lang)
        except OSError as exc:
            warn(self, 'Settings', 'Could not save language: ' + str(exc))
            return
        # Restart only the Python UI; the X11 session and Mainsail service remain alive.
        self.timer.stop()
        QTimer.singleShot(100, self._restart_now)

    def make_dashboard(self, l):
        grid = QGridLayout()
        for i, (title, unit, key) in enumerate([
            ('GESCHWINDIGKEIT', 'mm/s', 'speed'),
            ('MATERIALFLUSS', 'mm³/s', 'flow'),
            ('ACCEL-LIMIT', 'mm/s²', 'accel')]):
            f, c = card()
            c.addWidget(label(title, 'caption'))
            if key in ('speed', 'accel'):
                val = ClickableMetric('--')
                val.setObjectName('metric')
                val.setCursor(Qt.PointingHandCursor)
                val.clicked.connect(partial(self.edit_motion_limit,
                                            'velocity' if key == 'speed' else 'accel'))
            else:
                val = label('--', 'metric')
            self.s[key] = val
            c.addWidget(val)
            c.addWidget(label(unit, 'caption'))
            grid.addWidget(f, i // (1 if self.tiny else 2 if self.compact else 3),
                           i % (1 if self.tiny else 2 if self.compact else 3))
        l.addLayout(grid)
        # Live values stay large. The active motion limits are independently editable.
        self.motion_limit_buttons = {}
        motion_bar = QHBoxLayout()
        for key, caption in [('velocity', 'Max speed'), ('accel', 'Max accel')]:
            b = button(caption + ': --', partial(self.edit_motion_limit, key))
            b.setMinimumHeight(40)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.motion_limit_buttons[key] = b
            motion_bar.addWidget(b)
        motor_button = button('Motors Off', self.motors_off)
        motor_button.setMinimumHeight(40)
        motor_button.setFixedWidth(70 if self.compact else 96)
        motor_button.setToolTip(tr('Disables all stepper motors. Rehome axes before moving again.'))
        motor_button.setEnabled(False)
        self.motor_off_buttons.append(motor_button)
        motion_bar.addWidget(motor_button)
        l.addLayout(motion_bar)
        l.addWidget(label('Speed / Flow Overrides', 'caption'))
        self.add_override_controls(l)
        f, c = card('Druckstatus')
        self.s['print'] = label('Moonraker wird verbunden ...')
        c.addWidget(self.s['print'])
        l.addWidget(f)
        # Stable, compact temperature overview instead of a long ad-hoc text line.
        f, c = card('Temperaturen  ·  Übersicht')
        self.dash_temp_grid = QGridLayout()
        self.dash_temp_grid.setSpacing(6)
        c.addLayout(self.dash_temp_grid)
        self.dash_temp_widgets = {}
        self._dash_temp_signature = None
        self.dash_temp_empty = label('Temperaturen werden geladen ...', 'caption')
        c.addWidget(self.dash_temp_empty)
        c.addWidget(button('Alle Temperaturen ›', partial(self.navigate, 'Temperaturen')))
        l.addWidget(f)
        f, c = card('Temperature presets')
        self.dashboard_presets = QGridLayout()
        self.dashboard_presets.setSpacing(6)
        c.addLayout(self.dashboard_presets)
        c.addWidget(button('Edit presets', self.edit_presets))
        c.addWidget(label('Hotend presets use the currently selected tool; bed and chamber are independent.', 'caption'))
        l.addWidget(f)
        self.rebuild_presets()
        f, c = card('Homing  ·  getrennte Achsen')
        bar = QHBoxLayout()
        for axis in ['X', 'Y', 'Z', 'XY', 'ALLE']:
            cmd = 'G28' if axis == 'ALLE' else 'G28 ' + (axis if axis != 'XY' else 'X Y')
            bar.addWidget(button(axis, partial(self.confirm_script, cmd, 'Homing '+axis)))
        c.addLayout(bar)
        self.s['homed'] = label('Referenzierung: --', 'caption')
        c.addWidget(self.s['homed'])
        l.addWidget(f)

    def make_tools(self, l):
        f, c = card('Tool / Extruder')
        self.tool_selector = QComboBox()
        self.tool_selector.currentTextChanged.connect(self.update_tool)
        c.addWidget(self.tool_selector)
        self.s['tool_info'] = label('Extruder wird erkannt ...')
        c.addWidget(self.s['tool_info'])
        c.addWidget(label('Pressure Advance', 'caption'))
        self.pa_spin = TouchSpinBox()
        self.pa_spin.setProperty('touch_title', 'Pressure Advance')
        self.pa_spin.setDecimals(4)
        self.pa_spin.setSingleStep(.005)
        self.pa_spin.setRange(0, 1)
        c.addLayout(row(self.pa_spin, button('Übernehmen', self.apply_pa, True)))
        l.addWidget(f)
        f, c = card('Firmware Retract')
        self.retract_spins = {}
        for title, key, maximum in [('Länge (mm)', 'retract_length', 100),
                                     ('Speed (mm/s)', 'retract_speed', 100),
                                     ('Extra (mm)', 'unretract_extra_length', 100),
                                     ('Unretract Speed', 'unretract_speed', 100)]:
            spin = TouchSpinBox()
            spin.setProperty('touch_title', title)
            spin.setRange(0, maximum)
            spin.setDecimals(2)
            spin.setSingleStep(.1)
            self.retract_spins[key] = spin
            c.addLayout(row(label(title), spin))
        c.addLayout(row(button('Übernehmen', self.apply_retract, True),
                        button('Reset', self.reset_retract)))
        l.addWidget(f)
        f, c = card('Extrusion')
        c.addWidget(label('Extrusion nur bei ausreichend heißem, aktivem Hotend und ohne laufenden Druck. Klipper prüft zusätzlich seine Grenzwerte.', 'caption'))
        self.extrude_len = QComboBox()
        self.extrude_len.addItems([str(x).rstrip('0').rstrip('.') for x in options('extrusion', 'lengths', '1, 5, 10, 50, 100')])
        self.extrude_speed = QComboBox()
        self.extrude_speed.addItems([str(x).rstrip('0').rstrip('.') for x in options('extrusion', 'speeds', '2, 5, 10, 20, 50')])
        c.addLayout(row(label('Länge mm'), self.extrude_len, label('mm/s'), self.extrude_speed))
        c.addLayout(row(button('Extrudieren', partial(self.extrude, 1), True),
                        button('Zurückziehen', partial(self.extrude, -1))))
        l.addWidget(f)

    def make_temperatures(self, l):
        l.addWidget(label('Heizer mit Sollwert · Sensoren nur Anzeige', 'caption'))
        self.temp_box, self.temp_content = card()
        self.temp_info = label('Verbindung zu Moonraker wird hergestellt ...')
        self.temp_content.addWidget(self.temp_info)
        self.temperature_grid = QGridLayout()
        self.temperature_grid.setSpacing(7)
        self.temp_content.addLayout(self.temperature_grid)
        self.temp_widgets = {}
        l.addWidget(self.temp_box)
        f, c = card('Temperatur-Presets  ·  veloxscreen.conf')
        self.temp_preset_grid = QGridLayout()
        self.temp_preset_grid.setSpacing(7)
        c.addLayout(self.temp_preset_grid)
        c.addWidget(button('Edit presets', self.edit_presets))
        c.addWidget(label('Presets beziehen sich auf das gewählte Tool, Heizbett und ggf. Kammerheizer.', 'caption'))
        l.addWidget(f)
        self._last_temp_signature = None
        self.rebuild_presets()

    def make_control(self, l):
        f, c = card('Achsensteuerung')
        self.jog_step = QComboBox()
        self.jog_step.addItems([str(int(v)) if int(v)==v else str(v) for v in options('extrusion', 'jog_steps', '1, 5, 15, 25, 50')])
        c.addLayout(row(label('Schrittweite mm'), self.jog_step))
        grid = QGridLayout()
        for text, axis, direction, xx, yy in [('Y+', 'Y', 1, 1, 0), ('X-', 'X', -1, 0, 1),
                                              ('X+', 'X', 1, 2, 1), ('Y-', 'Y', -1, 1, 2),
                                              ('Z+', 'Z', 1, 3, 0), ('Z-', 'Z', -1, 3, 2)]:
            grid.addWidget(button(text, partial(self.jog, axis, direction)), yy, xx)
        c.addLayout(grid)
        self.s['position'] = label('Position X/Y/Z: --')
        c.addWidget(self.s['position'])
        motor_row = QHBoxLayout()
        motor_row.addWidget(label('Disables all stepper motors. Rehome axes before moving again.', 'caption'), 1)
        control_motor_button = button('Motors Off', self.motors_off)
        control_motor_button.setMinimumWidth(108)
        control_motor_button.setEnabled(False)
        self.motor_off_buttons.append(control_motor_button)
        motor_row.addWidget(control_motor_button)
        c.addLayout(motor_row)
        l.addWidget(f)
        l.addWidget(label('Speed / Flow Overrides', 'caption'))
        self.add_override_controls(l)
        l.addWidget(label('Flow override is global for the G-Code stream, including tool changes.', 'caption'))
        f, c = card('Lüfter  ·  automatisch erkannt')
        self.fan_area = QVBoxLayout()
        c.addLayout(self.fan_area)
        l.addWidget(f)
        self._last_fan_signature = None

    def make_led_effects(self, l):
        f, c = card('LED Effects · klipper-led_effect')
        c.addWidget(label('Effects are discovered automatically from Klipper. Active state is live.', 'caption'))
        c.addWidget(label('Fade duration', 'caption'))
        self.led_fade = QComboBox()
        self.led_fade.addItems(['0', '0.5', '1', '2', '3'])
        desired = conf('led_effects', 'fade_seconds', '0.5').strip()
        index = self.led_fade.findText(desired)
        self.led_fade.setCurrentIndex(index if index >= 0 else 1)
        self.led_fade.currentTextChanged.connect(lambda v: set_config('led_effects', 'fade_seconds', v))
        c.addWidget(self.led_fade)
        c.addWidget(button('Stop all effects', self.stop_all_led_effects))
        self.led_status_label = label('Waiting for Klipper LED effects ...', 'caption')
        c.addWidget(self.led_status_label)
        self.led_area = QVBoxLayout()
        self.led_area.setSpacing(7)
        c.addLayout(self.led_area)
        l.addWidget(f)

    def make_files(self, l):
        f, c = card('G-Code-Dateien auf Moonraker')
        self.files_list = QComboBox()
        c.addWidget(self.files_list)
        c.addLayout(row(button('Aktualisieren', lambda:self.network.load_files(None)),
                        button('Ausgewählten Druck starten', self.print_selected, True)))
        c.addWidget(label('Upload folgt in der nächsten Version. Datei-Start nur nach Bestätigung.', 'caption'))
        l.addWidget(f)

    def make_calib(self, l):
        f, c = card('Bed Mesh')
        self.s['mesh'] = label('No active bed mesh')
        c.addWidget(self.s['mesh'])
        self.mesh_heatmap = MeshHeatmap()
        self.mesh_heatmap.selected.connect(self.mesh_point_selected)
        c.addWidget(self.mesh_heatmap)
        self.mesh_cell_info = label('Tap any cell to inspect its measured Z value.', 'caption')
        c.addWidget(self.mesh_cell_info)
        c.addWidget(label('Blue = lower · White = near zero · Red = higher', 'caption'))
        c.addLayout(row(button('BED_MESH_CALIBRATE', partial(self.confirm_script, 'BED_MESH_CALIBRATE', 'Mesh-Messung')),
                        button('BED_MESH_CLEAR', partial(self.confirm_script, 'BED_MESH_CLEAR', 'Mesh löschen'))))
        l.addWidget(f)

    def make_console(self, l):
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(210)
        l.addWidget(self.console)
        self.console_input = TouchCommandInput(self)
        self.console_input.setPlaceholderText(tr('Tap to enter G-Code / macro'))
        l.addLayout(row(self.console_input,
                        button('Keyboard', self.console_input.open_keyboard),
                        button('Send', self.send_console, True)))
        l.addWidget(label('Befehle und Antworten, die über VeloxScreen gesendet werden. Vollständiges Moonraker-Event-Streaming folgt.', 'caption'))

    def make_macros(self, l):
        l.addWidget(label('Alle gcode_macro-Einträge aus Klipper werden automatisch erkannt. Ausblenden löscht kein Makro.', 'caption'))
        f, c = card('Makros ausführen')
        self.macro_buttons = QGridLayout()
        self.macro_buttons.setSpacing(7)
        c.addLayout(self.macro_buttons)
        l.addWidget(f)
        f, c = card('Sichtbarkeit · alle Makros')
        c.addLayout(row(
            button('Hide All', partial(self.set_all_macros_visible, False)),
            button('Show All', partial(self.set_all_macros_visible, True))))
        c.addWidget(label('Select only the macros you want to see. Newly added macros stay hidden after Hide All.', 'caption'))
        self.macro_filter = TouchCommandInput(self)
        self.macro_filter.setPlaceholderText(tr('Search macros...'))
        self.macro_filter.textChanged.connect(self._filter_macro_list)
        c.addLayout(row(self.macro_filter,
                        button('Keyboard', self.macro_filter.open_keyboard),
                        button('Clear search', self.macro_filter.clear)))
        self.macro_visibility = QVBoxLayout()
        self.macro_visibility.setSpacing(1)
        c.addLayout(self.macro_visibility)
        l.addWidget(f)
        self.macro_status = label('Warte auf Klipper-Konfiguration ...', 'caption')
        l.addWidget(self.macro_status)
        self._render_macros()

    def update_theme_summary(self):
        if hasattr(self, 'theme_preview_label'):
            self.theme_preview_label.setText('   '.join(
                f'{tr(THEME_LABELS[key])}: {self.theme[key].upper()}'
                for key in ('accent_color', 'overlay_color')))

    def edit_theme(self):
        ThemeColorDialog(self).exec_()

    def apply_theme(self, theme):
        global STYLE, THEME
        self.theme = dict(theme)
        THEME = dict(theme)
        STYLE = themed_style(THEME)
        self.setStyleSheet(scaled_style(STYLE, self.ui_scale))
        # Fan sliders use a custom stylesheet, so refresh it explicitly.
        for _name, (_text, slider) in getattr(self, 'fan_widgets', {}).items():
            if slider is not None:
                slider.setStyleSheet(fan_slider_style(THEME))
        self.update_theme_summary()

    def _build_notification_banner(self):
        # A non-modal overlay within VeloxScreen. It does not interrupt G-Code.
        self.notification_banner = QFrame(self)
        self.notification_banner.setObjectName('veloxNotification')
        self.notification_banner.setStyleSheet(
            'QFrame#veloxNotification {background: #203247; border: 2px solid #398ed1; border-radius: 10px;}'
            'QLabel {background: transparent; color: #f6f8fc; font-size: 16px; font-weight: 650;}'
            'QPushButton {min-width: 30px; max-width: 30px; background: #344e69; padding: 2px;}')
        lay = QHBoxLayout(self.notification_banner)
        lay.setContentsMargins(10, 5, 5, 5)
        lay.setSpacing(5)
        self.notification_text = QLabel('')
        self.notification_text.setWordWrap(True)
        lay.addWidget(self.notification_text, 1)
        close = QPushButton('×')
        close.setFixedSize(32, 30)
        close.clicked.connect(self.hide_notification)
        lay.addWidget(close)
        self.notification_banner.hide()
        self._position_notification_banner()

    def _position_notification_banner(self):
        if hasattr(self, 'notification_banner'):
            # Keep the sidebar usable on both 4.3-inch and 5-inch 800x480 panels.
            width = min(475, max(270, self.width() - 178))
            # Size for the larger text, including multi-line RESPOND messages.
            # The notification stays non-modal and never covers the whole display.
            available_text_width = max(120, width - 74)
            text = self.notification_text.text()
            text_height = 0
            if text:
                text_height = self.notification_text.fontMetrics().boundingRect(
                    QRect(0, 0, available_text_width, 1000),
                    Qt.TextWordWrap, text).height()
            height = min(170, max(88, text_height + 24))
            self.notification_banner.setGeometry(self.width() - width - 8, 8, width, height)

    def show_notification(self, message, force=False):
        if not force and not self.notifications_enabled:
            return
        splash = getattr(self, '_splash_overlay', None)
        if splash is not None and splash.isVisible() and not force:
            return
        message = str(message).strip()[:230]
        if not message:
            return
        now = time.monotonic()
        if message == self._last_notification[0] and now - self._last_notification[1] < 2.0:
            return
        self._last_notification = (message, now)
        self.notification_text.setText(message)
        self._position_notification_banner()
        self.notification_banner.show()
        self.notification_banner.raise_()
        self._notification_timer.start(self.notification_duration * 1000)

    def hide_notification(self):
        if hasattr(self, 'notification_banner'):
            self.notification_banner.hide()
        self._notification_timer.stop()

    @staticmethod
    def _displayable_response(raw):
        """Do not mistake G-Code command echoes or routine probes for macro popups."""
        msg = str(raw).strip()
        if not msg or msg.lower() in ('ok', 'ok\n'):
            return ''
        if msg.startswith('//'):
            msg = msg[2:].strip()
        if msg.lower().startswith('echo:'):
            msg = msg[5:].strip()
        if msg.startswith('!!'):
            msg = msg[2:].strip()
        if not msg or msg.lower().startswith(('klipper state:', 'mcu shutdown:')):
            return ''
        return msg

    def handle_gcode_messages(self, entries):
        """Real Moonraker/Klipper responses; manual and slicer-triggered too."""
        if not isinstance(entries, list):
            return
        for entry in entries:
            if entry.get('type') != 'response':
                continue
            text = str(entry.get('message', '')).strip()
            if not text:
                continue
            self.log(text)
            readable = self._displayable_response(text)
            if readable:
                self.show_notification(readable)

    def change_notifications(self, checked):
        previous = self.notifications_enabled
        self.notifications_enabled = bool(checked)
        try:
            set_config('notifications', 'enabled', 'true' if checked else 'false')
        except OSError as exc:
            self.notifications_enabled = previous
            self.notifications_checkbox.blockSignals(True)
            self.notifications_checkbox.setChecked(previous)
            self.notifications_checkbox.blockSignals(False)
            warn(self, 'Settings', 'Could not save config: ' + str(exc))
        if not self.notifications_enabled:
            self.hide_notification()

    def change_notification_duration(self, index):
        value = self.notification_duration_combo.itemData(index)
        if value is None:
            return
        old = self.notification_duration
        self.notification_duration = int(value)
        try:
            set_config('notifications', 'duration_seconds', str(value))
        except OSError as exc:
            self.notification_duration = old
            warn(self, 'Settings', 'Could not save config: ' + str(exc))

    def make_network(self, l):
        f, c = card('Wi-Fi / NetworkManager')
        self.wifi_status = label('Tap Scan to find Wi-Fi networks.', 'caption')
        c.addWidget(self.wifi_status)
        self.wifi_ip = label('IP address: --', 'caption')
        c.addWidget(self.wifi_ip)
        self.wifi_scan_button = button('Scan Wi-Fi', self.scan_wifi, True)
        c.addWidget(self.wifi_scan_button)
        c.addWidget(label('Connecting to another Wi-Fi can interrupt SSH and Moonraker access.', 'caption'))
        l.addWidget(f)
        f, c = card('Available networks')
        self.wifi_networks_area = QVBoxLayout()
        c.addLayout(self.wifi_networks_area)
        c.addWidget(label('Requires NetworkManager (nmcli). WPA-Personal/open networks only. '
                          'Enterprise and WPA3-only networks may require manual configuration.', 'caption'))
        l.addWidget(f)

    def scan_wifi(self):
        if self._wifi_busy:
            return
        self._wifi_busy = True
        self.wifi_scan_button.setEnabled(False)
        self.wifi_status.setText('Scanning Wi-Fi networks...')
        def work():
            try:
                self.wifi_signals.scanned.emit(wifi_manager.network_info())
            except Exception as exc:
                self.wifi_signals.failed.emit(str(exc))
        threading.Thread(target=work, daemon=True).start()

    def wifi_scanned(self, info):
        self._wifi_busy = False
        self.wifi_scan_button.setEnabled(True)
        self._wifi_info = info
        self.wifi_status.setText('Wi-Fi device: ' + info['iface'])
        self.wifi_ip.setText('IP address: ' + info['ip'])
        while self.wifi_networks_area.count():
            item = self.wifi_networks_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        networks = info['networks']
        if not networks:
            self.wifi_networks_area.addWidget(label('No visible networks found. Try Scan again.'))
        for net in networks:
            security = 'Open' if net['security'] in ('', '--') else 'Secured'
            title = '{}  ·  {}%  ·  {}{}'.format(net['ssid'], net['signal'], security,
                                                   '  ✓ Connected' if net['active'] else '')
            b = button(title, partial(self.choose_wifi, dict(net)))
            b.setToolTip(net['ssid'])
            b.setEnabled(not net['active'])
            self.wifi_networks_area.addWidget(b)

    def choose_wifi(self, network):
        if self._wifi_busy or not self._wifi_info:
            return
        if network['active']:
            return
        ssid = network['ssid']
        secured = network['security'] not in ('', '--')
        question = ('Connect to "{}"?\n\nChanging networks may disconnect this touchscreen '
                    'from Moonraker or interrupt your SSH connection.').format(ssid)
        if QMessageBox.question(self, 'Change Wi-Fi', question,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        password = ''
        if secured:
            dialog = TouchCommandDialog(self, '')
            dialog.setWindowTitle('Wi-Fi password · ' + ssid)
            dialog.entry.setPlaceholderText('Wi-Fi password (leave blank for saved profile)')
            dialog.entry.setEchoMode(QLineEdit.Password)
            # Avoid showing the secret in a generic 'Use command' context.
            dialog.layout().itemAt(dialog.layout().count()-1).layout().itemAt(1).widget().setText('Connect')
            if dialog.exec_() != QDialog.Accepted:
                return
            password = dialog.entry.text()
            dialog.entry.clear()
        self._wifi_busy = True
        self.wifi_scan_button.setEnabled(False)
        self.wifi_status.setText('Connecting to ' + ssid + ' ...')
        iface = self._wifi_info['iface']
        def work():
            try:
                self.wifi_signals.connected.emit(
                    wifi_manager.connect_wifi(iface, ssid, password, secured))
            except Exception as exc:
                self.wifi_signals.failed.emit(str(exc))
        threading.Thread(target=work, daemon=True).start()

    def wifi_connected(self, message):
        self._wifi_busy = False
        self.wifi_scan_button.setEnabled(True)
        self.wifi_status.setText(message)
        self.scan_wifi()

    def wifi_failed(self, error):
        self._wifi_busy = False
        self.wifi_scan_button.setEnabled(True)
        self.wifi_status.setText('Wi-Fi error: ' + error)
        warn(self, 'Wi-Fi', error + '\n\nIf permissions are denied, check NetworkManager '
             'and PolicyKit permissions for the VeloxScreen user. No root access is granted by VeloxScreen.')

    def make_settings(self, l):
        f, c = card('Startup screen')
        self.show_splash_checkbox = TouchCheckBox(tr('Show startup screen'))
        self.show_splash_checkbox.setChecked(startup_bool('show_splash', True))
        self.show_splash_checkbox.setMinimumHeight(35)
        c.addWidget(self.show_splash_checkbox)
        self.speed_lines_checkbox = TouchCheckBox(tr('Speed-line animation'))
        self.speed_lines_checkbox.setChecked(startup_bool('animate_speedlines', True))
        self.speed_lines_checkbox.setMinimumHeight(35)
        c.addWidget(self.speed_lines_checkbox)
        duration_row = QHBoxLayout()
        duration_row.addWidget(label('Startup duration'))
        self.startup_duration_combo = QComboBox()
        self.startup_duration_combo.setMinimumHeight(37)
        for seconds in (1, 2, 3, 5):
            self.startup_duration_combo.addItem(f'{seconds} s', seconds)
        existing_duration = int(fval(conf('startup', 'duration_seconds', '3'), 3))
        combo_idx = self.startup_duration_combo.findData(existing_duration)
        self.startup_duration_combo.setCurrentIndex(max(0, combo_idx))
        duration_row.addWidget(self.startup_duration_combo)
        c.addLayout(duration_row)
        c.addWidget(label('The logo transitions smoothly to the dashboard. Tap to skip.', 'caption'))
        controls = QHBoxLayout()
        controls.addWidget(button('Preview splash', self.preview_startup_splash))
        controls.addWidget(button('Save startup settings', self.save_startup_settings, True))
        c.addLayout(controls)
        self.startup_status = label('', 'caption')
        c.addWidget(self.startup_status)
        l.addWidget(f)
        f, c = card('Appearance / theme')
        c.addWidget(label('Choose any RGB color for accents, panels, backgrounds, sidebar and buttons.'))
        self.theme_preview_label = label('')
        c.addWidget(self.theme_preview_label)
        c.addWidget(button('Edit theme colors', self.edit_theme, True))
        l.addWidget(f)
        self.update_theme_summary()
        f, c = card('Language / Sprache')
        self.lang_combo = QComboBox()
        self.lang_combo.addItem('English', 'en')
        self.lang_combo.addItem('Deutsch', 'de')
        self.lang_combo.setCurrentIndex(0 if LANGUAGE == 'en' else 1)
        c.addWidget(self.lang_combo)
        c.addWidget(label('Default: English. Changing the language restarts the UI.'))
        c.addWidget(button('Apply language', self.apply_language, True))
        l.addWidget(f)
        f, c = card('Bestätigungsdialoge')
        self.confirm_checkbox = TouchCheckBox(tr('Vor G-Code-Befehlen bestätigen'))
        self.confirm_checkbox.setMinimumHeight(46)
        self.confirm_checkbox.setChecked(self.confirm_enabled)
        self.confirm_checkbox.toggled.connect(self.change_confirmation)
        c.addWidget(self.confirm_checkbox)
        c.addWidget(label('Aus = Befehle werden beim Tippen direkt gesendet. Ein = Nachfrage vor jedem Befehl. Einstellung bleibt gespeichert.', 'caption'))
        l.addWidget(f)
        f, c = card(tr('G-Code notifications'))
        self.notifications_checkbox = TouchCheckBox(tr('Show RESPOND / macro messages'))
        self.notifications_checkbox.setMinimumHeight(41)
        self.notifications_checkbox.setChecked(self.notifications_enabled)
        self.notifications_checkbox.toggled.connect(self.change_notifications)
        c.addWidget(self.notifications_checkbox)
        c.addWidget(label('Show Klipper RESPOND output as a non-blocking popup, including IDEX toolchanges. Macros must emit RESPOND to provide a message.', 'caption'))
        duration_row = QHBoxLayout()
        duration_row.addWidget(label('Popup duration'))
        self.notification_duration_combo = QComboBox()
        for seconds in (2, 3, 4, 5, 8, 10):
            self.notification_duration_combo.addItem(f'{seconds} s', seconds)
        idx = self.notification_duration_combo.findData(self.notification_duration)
        self.notification_duration_combo.setCurrentIndex(max(0, idx))
        self.notification_duration_combo.currentIndexChanged.connect(self.change_notification_duration)
        duration_row.addWidget(self.notification_duration_combo)
        c.addLayout(duration_row)
        c.addWidget(button('Test popup', lambda: self.show_notification('IDEX: T1 active (test)', force=True)))
        l.addWidget(f)
        f, c = card('Service Control')
        c.addWidget(button('VeloxScreen neu starten', self.restart_screen, True))
        c.addWidget(label('Neustart direkt auf dem Touchdisplay. Alternativ über Mainsail → Service Control → veloxscreen (nach einmaliger Einrichtung).', 'caption'))
        l.addWidget(f)
        f, c = card('Konfiguration')
        c.addWidget(label(str(CFG_PATH), 'caption'))
        c.addWidget(label('Makros ein-/ausblenden unter Makros. Die Einstellungen werden in veloxscreen.conf gespeichert.', 'caption'))
        l.addWidget(f)

    def change_confirmation(self, checked):
        previous = self.confirm_enabled
        self.confirm_enabled = bool(checked)
        try:
            set_config('behavior', 'confirm_commands', 'true' if checked else 'false')
        except OSError as exc:
            self.confirm_enabled = previous
            self.confirm_checkbox.blockSignals(True)
            self.confirm_checkbox.setChecked(previous)
            self.confirm_checkbox.blockSignals(False)
            warn(self, 'Einstellungen', 'Config konnte nicht gespeichert werden: ' + str(exc))

    def refresh_macros(self, status):
        settings = status.get('configfile', {}).get('settings', {})
        commands = {}
        for key in settings:
            if key.lower().startswith('gcode_macro '):
                macro = key.split(' ', 1)[1].strip().upper()
                if macro:
                    commands[macro] = macro
        # Custom shortcuts may contain more than a macro name.
        if config.has_section('macros'):
            for name, command in config.items('macros'):
                if name.strip() and command.strip():
                    commands.setdefault(name.strip(), command.strip())
        if commands != self.macro_commands:
            self.macro_commands = commands
            self._render_macros()

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            if item.layout():
                self._clear_layout(item.layout())

    def macro_is_visible(self, name):
        key = name.strip().upper()
        if self.macro_visibility_mode == 'selected':
            return key in self.selected_macros
        return key not in self.hidden_macros

    def _save_macro_visibility(self):
        # Write all three options together in a single atomic config update.
        if not config.has_section('macro_visibility'):
            config.add_section('macro_visibility')
        config.set('macro_visibility', 'mode', self.macro_visibility_mode)
        config.set('macro_visibility', 'visible', ', '.join(sorted(self.selected_macros)))
        config.set('macro_visibility', 'hidden', ', '.join(sorted(self.hidden_macros)))
        save_config()

    def _render_macro_buttons(self):
        if not hasattr(self, 'macro_buttons'):
            return
        self._clear_layout(self.macro_buttons)
        names = sorted(self.macro_commands, key=str.casefold)
        shown = 0
        for name in names:
            if self.macro_is_visible(name):
                self.macro_buttons.addWidget(
                    button(name, partial(self.confirm_script, self.macro_commands[name], name)),
                    shown // (1 if self.tiny else 2), shown % (1 if self.tiny else 2))
                shown += 1
        if hasattr(self, 'macro_status'):
            self.macro_status.setText(tr(
                f'{shown} sichtbar · {len(names)} erkannt · {len(names)-shown} ausgeblendet'
                if names else 'Keine Makros gefunden oder Klipper noch nicht verbunden.'))

    def _render_macros(self):
        if not hasattr(self, 'macro_buttons'):
            return
        self._clear_layout(self.macro_visibility)
        self.macro_check_widgets = {}
        for name in sorted(self.macro_commands, key=str.casefold):
            cb = TouchCheckBox(name)
            cb.setChecked(self.macro_is_visible(name))
            cb.toggled.connect(partial(self.set_macro_visible, name))
            self.macro_visibility.addWidget(cb)
            self.macro_check_widgets[name] = cb
        self._filter_macro_list()
        self._render_macro_buttons()

    def _filter_macro_list(self, *_):
        """Filter toggles only; runnable macros remain based on visibility."""
        query = self.macro_filter.text().strip().casefold() if hasattr(self, 'macro_filter') else ''
        for name, checkbox in self.macro_check_widgets.items():
            checkbox.setVisible(query in name.casefold())

    def set_all_macros_visible(self, visible):
        previous = (self.macro_visibility_mode,
                    set(self.hidden_macros), set(self.selected_macros))
        if visible:
            self.macro_visibility_mode = 'all'
            self.hidden_macros.clear()
            self.selected_macros.clear()
        else:
            self.macro_visibility_mode = 'selected'
            self.selected_macros.clear()
            self.hidden_macros.clear()
        try:
            self._save_macro_visibility()
        except OSError as exc:
            self.macro_visibility_mode, self.hidden_macros, self.selected_macros = previous
            warn(self, 'Macros', 'Visibility not saved: ' + str(exc))
            return
        # Block signals: bulk actions must save only once and never run macros.
        for name, checkbox in self.macro_check_widgets.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(self.macro_is_visible(name))
            checkbox.blockSignals(False)
        self._render_macro_buttons()

    def set_macro_visible(self, name, visible):
        previous_hidden = set(self.hidden_macros)
        previous_selected = set(self.selected_macros)
        key = name.upper()
        target = self.selected_macros if self.macro_visibility_mode == 'selected' else self.hidden_macros
        if self.macro_visibility_mode == 'selected':
            if visible:
                target.add(key)
            else:
                target.discard(key)
        else:
            if visible:
                target.discard(key)
            else:
                target.add(key)
        try:
            self._save_macro_visibility()
        except OSError as exc:
            self.hidden_macros = previous_hidden
            self.selected_macros = previous_selected
            checkbox = self.macro_check_widgets.get(name)
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(self.macro_is_visible(name))
                checkbox.blockSignals(False)
            warn(self, 'Macros', 'Visibility not saved: ' + str(exc))
            return
        self._render_macro_buttons()

    def restart_screen(self):
        if self.confirm_enabled and QMessageBox.question(self, 'VeloxScreen', tr('VeloxScreen jetzt neu starten?'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.timer.stop()
        QTimer.singleShot(100, self._restart_now)

    def _restart_now(self):
        # Replaces this process, preserving X11's client PID (no systemd/root needed).
        os.execv(sys.executable, [sys.executable, str(BASE / 'main.py')])

    def error(self, msg):
        self.connection.setText(tr('● Verbindung gestört'))
        self.log('FEHLER: ' + msg)

    def response(self, msg):
        if msg.startswith('__FILES__'):
            try:
                files = json.loads(msg[len('__FILES__'):])
                self.files_list.clear()
                for f in sorted(files, key=lambda x: x.get('modified', 0), reverse=True):
                    if f.get('path', '').lower().endswith(('.gcode', '.gco')):
                        self.files_list.addItem(f['path'])
            except Exception as exc:
                self.error(str(exc))
        else:
            self.log(msg)

    def log(self, text):
        self.console.appendPlainText(text)
        # Limit logs in small-memory Pi environment.
        if self.console.blockCount() > 250:
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def refresh(self, data):
        self.connection.setText(tr('● Moonraker verbunden'))
        self.all_objects = data['objects']
        self.refresh_macros(data['status'])
        self.s = {**self.s, **data['status']}
        st = data['status']
        motion = st.get('motion_report', {})
        toolhead = st.get('toolhead', {})
        extruder_speed = fval(motion.get('live_extruder_velocity'))
        active = toolhead.get('extruder', 'extruder')
        filament = fval(st.get('configfile', {}).get('settings', {}).get(active, {}).get('filament_diameter'), 1.75)
        flow = extruder_speed * 3.141592653589793 * (filament / 2.0) ** 2
        self.set_text('speed', fmt(motion.get('live_velocity')))
        self.set_text('flow', fmt(abs(flow)))
        self.set_text('accel', fmt(toolhead.get('max_accel'), 0))
        self.motion_limit_buttons['velocity'].setText(tr('Max speed') + ': ' + fmt(toolhead.get('max_velocity'), 0) + ' mm/s  ✎')
        self.motion_limit_buttons['accel'].setText(tr('Max accel') + ': ' + fmt(toolhead.get('max_accel'), 0) + ' mm/s²  ✎')
        ps = st.get('print_stats', {})
        # Block M84 for active/paused prints, and until Klipper state is known.
        idle = (isinstance(ps, dict) and bool(ps.get('state'))
                and ps.get('state') not in ('printing', 'paused'))
        for motor_button in self.motor_off_buttons:
            motor_button.setEnabled(idle)
        vs = st.get('virtual_sdcard', {})
        self.set_text('print', f"{ps.get('state', 'Unbekannt')}   ·   {ps.get('filename') or 'Kein Druck'}   ·   {fmt(fval(vs.get('progress'))*100, 0)} %")
        axes = toolhead.get('homed_axes', '')
        self.set_text('homed', 'Referenziert: ' + (axes.upper() if axes else 'keine Achse'))
        gm = st.get('gcode_move', {})
        self.update_overrides(gm)
        self.set_text('position', 'Position X/Y/Z: ' + ', '.join(fmt(x,2) for x in gm.get('gcode_position', [])[:3]))
        self.update_dashboard_temperatures(st)
        self.update_discovered_tools(st)
        self.update_temps(st)
        self.update_fans(st)
        self.update_led_effects(st)
        self.update_calib(st)

    def set_text(self, key, text):
        obj = self.s.get(key)
        if isinstance(obj, QLabel):
            obj.setText(tr(text))

    def update_dashboard_temperatures(self, st):
        """Prioritize meaningful heaters/chamber sensors in fixed 2-column tiles.

        Sensor-only items have no target; additional sensors live in Temperaturen.
        """
        all_names = self.all_objects
        extruders = sorted((n for n in all_names if n == 'extruder' or re.fullmatch(r'extruder\d+', n)),
                           key=lambda n: (n != 'extruder', int(n[8:]) if n != 'extruder' else 0))
        chosen = extruders[:2]
        if 'heater_bed' in all_names:
            chosen.append('heater_bed')
        chambers = [n for n in all_names if n.startswith(('heater_generic ', 'temperature_sensor ', 'temperature_fan '))
                    and any(x in n.lower() for x in ('chamber', 'kammer'))]
        # Heaters first, then sensor-only readings; avoid random hardware sensor names.
        chambers.sort(key=lambda n: (0 if n.startswith('heater_generic ') else 1, n.lower()))
        if chambers:
            chosen.append(chambers[0])
        if len(chosen) < 4:
            chosen.extend(n for n in all_names if n.startswith('heater_generic ') and n not in chosen)
        chosen = chosen[:4]
        signature = tuple(chosen)
        if signature != self._dash_temp_signature:
            self._dash_temp_signature = signature
            self.dash_temp_widgets = {}
            while self.dash_temp_grid.count():
                item = self.dash_temp_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for i, name in enumerate(chosen):
                tile = QFrame()
                tile.setObjectName('card')
                tile_lay = QVBoxLayout(tile)
                tile_lay.setContentsMargins(8, 5, 8, 5)
                tile_lay.setSpacing(2)
                title = ('T0 · Hotend' if name == 'extruder' else
                         f'T{name[8:]} · Hotend' if re.fullmatch(r'extruder\d+', name) else
                         'Heizbett' if name == 'heater_bed' else
                         name.split(' ', 1)[1])
                tile_lay.addWidget(label(title, 'tempName'))
                value = label('-- °C', 'tempValue')
                value.setWordWrap(False)
                subtitle = label('Warte auf Daten', 'caption')
                tile_lay.addWidget(value)
                tile_lay.addWidget(subtitle)
                self.dash_temp_widgets[name] = (value, subtitle)
                self.dash_temp_grid.addWidget(tile, i // (1 if self.tiny else 2), i % (1 if self.tiny else 2))
        self.dash_temp_empty.setVisible(not bool(chosen))
        if not chosen:
            self.dash_temp_empty.setText(tr('Keine passenden Heizungen oder Kammersensoren gefunden.'))
        for name, (value, subtitle) in self.dash_temp_widgets.items():
            entry = st.get(name, {})
            value.setText(fmt(entry.get('temperature')) + ' °C')
            if name == 'heater_bed' or name == 'extruder' or re.fullmatch(r'extruder\d+', name) or name.startswith('heater_generic '):
                subtitle.setText(tr('Soll ') + fmt(entry.get('target'), 0) + ' °C')
            elif name.startswith('temperature_fan '):
                subtitle.setText(tr('Temperaturgeregelter Lüfter'))
            else:
                subtitle.setText(tr('Nur Sensor · keine Steuerung'))

    def update_discovered_tools(self, st):
        detected = sorted([x for x in self.all_objects if x == 'extruder' or re.fullmatch(r'extruder\d+', x)],
                          key=lambda x: (x != 'extruder', x))
        if detected != [self.tool_selector.itemData(i) for i in range(self.tool_selector.count())]:
            self.tool_selector.blockSignals(True)
            selected = self.tool_selector.currentData()
            self.tool_selector.clear()
            mapping = dict(config.items('tools')) if config.has_section('tools') else {}
            if mapping:
                for name, obj in mapping.items():
                    if obj in detected:
                        self.tool_selector.addItem(name + ' · ' + obj, obj)
            else:
                for i, obj in enumerate(detected):
                    self.tool_selector.addItem('T' + str(i) + ' · ' + obj, obj)
            for i in range(self.tool_selector.count()):
                if self.tool_selector.itemData(i) == selected:
                    self.tool_selector.setCurrentIndex(i)
            self.tool_selector.blockSignals(False)
        active = st.get('toolhead', {}).get('extruder')
        # Display actual active extruder; tool command T0/T1 is not assumed to exist.
        selected = self.tool_selector.currentData() or active or 'extruder'
        info = st.get(selected, {})
        cfg = st.get('configfile', {}).get('settings', {}).get(selected, {})
        self.set_text('tool_info', f"Aktiver Extruder: {active}  ·  {selected}: {fmt(info.get('temperature'))} / {fmt(info.get('target'))} °C\nDüse: {cfg.get('nozzle_diameter','--')} mm  ·  PA: {fmt(info.get('pressure_advance'), 4)}")
        incoming_pa = fval(info.get('pressure_advance'))
        if self.pa_spin.property('velox_dirty'):
            if abs(self.pa_spin.value() - incoming_pa) < 0.00006:
                self.pa_spin.setProperty('velox_dirty', False)
        else:
            self.pa_spin.setValue(incoming_pa)
        retract = st.get('firmware_retraction', {})
        for key, spin in self.retract_spins.items():
            if key in retract:
                received = fval(retract[key])
                if spin.property('velox_dirty'):
                    if abs(spin.value() - received) < 0.0006:
                        spin.setProperty('velox_dirty', False)
                else:
                    spin.setValue(received)

    def update_tool(self, _value=None):
        # Selected tool means setting target extruder for PA; no automatic tool change.
        pass

    def apply_pa(self):
        extr = self.tool_selector.currentData()
        if extr:
            self.confirm_script(f'SET_PRESSURE_ADVANCE EXTRUDER={extr} ADVANCE={self.pa_spin.value():.4f}', 'Pressure Advance')

    def apply_retract(self):
        values = self.retract_spins
        self.confirm_script('SET_RETRACTION RETRACT_LENGTH={:.3f} RETRACT_SPEED={:.3f} UNRETRACT_EXTRA_LENGTH={:.3f} UNRETRACT_SPEED={:.3f}'.format(
            *(values[k].value() for k in ('retract_length', 'retract_speed', 'unretract_extra_length', 'unretract_speed'))),
            'Firmware Retract')

    def reset_retract(self):
        st = self.s.get('configfile', {}).get('config', {}).get('firmware_retraction')
        if not st:
            warn(self, 'Firmware Retract', 'Keine [firmware_retraction]-Sektion in der geladenen Klipper-Konfiguration gefunden.')
            return
        keys = ('retract_length', 'retract_speed', 'unretract_extra_length', 'unretract_speed')
        defaults = {'retract_length': 0.0, 'retract_speed': 20.0, 'unretract_extra_length': 0.0, 'unretract_speed': 10.0}
        for key in keys:
            self.retract_spins[key].setValue(fval(st.get(key), defaults[key]))
            self.retract_spins[key].setProperty('velox_dirty', True)
        self.apply_retract()

    def extrude(self, direction):
        st = self.s
        if st.get('print_stats', {}).get('state') in ('printing','paused'):
            warn(self, 'Extrusion', 'Manuelles Extrudieren während Druck/Pause gesperrt.')
            return
        extr = self.tool_selector.currentData()
        active = st.get('toolhead', {}).get('extruder', 'extruder')
        if not extr or extr != active:
            warn(self, 'Extrusion', 'Wähle den tatsächlich aktiven Extruder. Toolwechsel über konfigurierte T-Makros.')
            return
        current = fval(st.get(extr, {}).get('temperature'))
        cfg = st.get('configfile', {}).get('settings', {}).get(extr, {})
        minimum = fval(cfg.get('min_extrude_temp'), 170)
        if current < minimum:
            warn(self, 'Extrusion', f'Hotend nur {current:.1f} °C, nötig mindestens {minimum:.1f} °C.')
            return
        length = fval(self.extrude_len.currentText())
        speed = fval(self.extrude_speed.currentText())
        if not 0 < length <= 150 or not 0 < speed <= 50:
            return
        amount = length * direction
        command = 'SAVE_GCODE_STATE NAME=VELOX_EXTRUDE\nM83\nG1 E{:.3f} F{:.0f}\nRESTORE_GCODE_STATE NAME=VELOX_EXTRUDE'.format(amount, speed*60)
        self.confirm_script(command, ('Extrudieren' if direction > 0 else 'Zurückziehen') + f' {length} mm bei {speed} mm/s')

    def apply_temp_preset(self, name, string):
        requested = {}
        for p in string.split(','):
            if ':' in p:
                k, v = p.strip().split(':',1)
                try:
                    requested[k.strip().lower()] = float(v.strip())
                except ValueError:
                    pass
        st = self.s
        heaters = [n for n in self.all_objects if n == 'extruder' or re.fullmatch(r'extruder\d+',n)
                   or n == 'heater_bed' or n.startswith('heater_generic ')]
        active = st.get('toolhead', {}).get('extruder', 'extruder')
        commands = []
        for purpose, value in requested.items():
            if purpose == 'nozzle':
                heater = self.tool_selector.currentData() or active
            elif purpose == 'bed':
                heater = 'heater_bed'
            elif purpose == 'chamber':
                explicit = conf('layout', 'chamber_heater').strip()
                candidates = ([explicit] if explicit in heaters else
                              [x for x in heaters if x.startswith('heater_generic ') and
                               any(word in x.lower() for word in ('chamber', 'kammer'))])
                heater = candidates[0] if candidates else None
            else:
                continue
            if heater not in heaters:
                continue
            cfg = st.get('configfile', {}).get('settings', {}).get(heater, {})
            maximum = fval(cfg.get('max_temp'), 300)
            if 0 <= value < maximum:
                commands.append(f'SET_HEATER_TEMPERATURE HEATER={heater.split(" ",1)[-1] if heater.startswith("heater_generic ") else heater} TARGET={value:g}')
        if commands:
            self.confirm_script('\n'.join(commands), 'Temperatur-Preset '+name)
        else:
            inform(self, 'Preset', 'Keine passenden regelbaren Heizungen für dieses Preset gefunden.')

    def update_temps(self, st):
        heaters = [x for x in self.all_objects if x == 'extruder' or re.fullmatch(r'extruder\d+',x)
                   or x == 'heater_bed' or x.startswith('heater_generic ')]
        sensors = [x for x in self.all_objects if x.startswith(('temperature_sensor ','temperature_fan '))]
        signature = (tuple(heaters),tuple(sensors))
        if signature != self._last_temp_signature:
            self._last_temp_signature=signature
            self.temp_widgets={}
            while self.temperature_grid.count():
                item=self.temperature_grid.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            for i,name in enumerate(heaters+sensors):
                is_heater=name in heaters
                f,c=card()
                pretty=name.replace('temperature_sensor ','').replace('heater_generic ','').replace('temperature_fan ','')
                if name=='heater_bed': pretty='Heizbett'
                if name=='extruder': pretty='T0 · Hotend'
                if re.fullmatch(r'extruder\d+',name): pretty='T'+name[len('extruder'):]+' · Hotend'
                name_label=label(pretty)
                name_label.setStyleSheet('font-weight: 650; font-size: 12px;')
                c.addWidget(name_label)
                temperature=label('-- °C','metric')
                temperature.setWordWrap(False)
                target=label('Sensor' if not is_heater else 'Soll -- °C','caption')
                readings=QHBoxLayout()
                readings.setSpacing(5)
                readings.addWidget(temperature, 0)
                readings.addWidget(target, 1, Qt.AlignVCenter)
                c.addLayout(readings)
                if is_heater:
                    set_button=button('Temperatur setzen', partial(self.set_heater,name),True)
                    c.addWidget(set_button)
                elif name.startswith('temperature_fan '):
                    target.setText(tr('Temp.-Lüfter'))
                else:
                    target.setText(tr('Nur Anzeige'))
                self.temp_widgets[name]=(temperature,target)
                self.temperature_grid.addWidget(f,i//(1 if self.tiny else 2),i%(1 if self.tiny else 2))
            self.temp_info.setVisible(not (heaters or sensors))
            if not (heaters or sensors): self.temp_info.setText(tr('Keine Temperaturobjekte erkannt.'))
        for name,(reading,target) in self.temp_widgets.items():
            data=st.get(name,{})
            reading.setText(fmt(data.get('temperature'))+' °C')
            if name in heaters:
                target.setText(tr('Soll: ')+fmt(data.get('target'))+' °C')
            elif name.startswith('temperature_fan '):
                target.setText(tr('Temp.-Lüfter'))

    def set_heater(self, heater):
        cfg=self.s.get('configfile',{}).get('settings',{}).get(heater,{})
        maximum=fval(cfg.get('max_temp'),300)
        upper=max(0,maximum-1)
        value,ok=TouchNumberDialog.get_value(self,'Solltemperatur · '+heater,
                fval(self.s.get(heater,{}).get('target')),0,upper,1)
        if ok:
            name=heater.split(' ',1)[-1] if heater.startswith('heater_generic ') else heater
            self.confirm_script(f'SET_HEATER_TEMPERATURE HEATER={name} TARGET={value:g}','Heizung '+heater)

    def update_fans(self, st):
        fans = [x for x in self.all_objects if x == 'fan' or x.startswith(
            ('fan_generic ', 'heater_fan ', 'temperature_fan '))]
        desired = [x.strip() for x in conf('layout', 'fan_order').split(',') if x.strip()]
        fans.sort(key=lambda f: (0, desired.index(f)) if f in desired else (1, f))
        signature = tuple(fans)
        if signature != self._last_fan_signature:
            self._last_fan_signature = signature
            for timer in self._fan_timers.values():
                timer.stop()
                timer.deleteLater()
            self._fan_timers = {}
            self._fan_pending.clear()
            self._fan_last_sent.clear()
            self.fan_widgets = {}
            while self.fan_area.count():
                item = self.fan_area.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for name in fans:
                frame, layout = card(name)
                value_label = label('— %', 'metric')
                layout.addWidget(value_label)
                if name == 'fan' or name.startswith('fan_generic '):
                    slider = QSlider(Qt.Horizontal)
                    slider.setRange(0, 100)
                    slider.setSingleStep(1)
                    slider.setPageStep(5)
                    slider.setMinimumHeight(40)
                    slider.setStyleSheet(fan_slider_style(self.theme))
                    slider.valueChanged.connect(partial(self.fan_slider_changed, name))
                    layout.addWidget(slider)
                    slider.sliderReleased.connect(partial(self.fan_slider_released, name))
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(partial(self.apply_fan, name))
                    self._fan_timers[name] = timer
                    self.fan_widgets[name] = (value_label, slider)
                else:
                    layout.addWidget(label('Automatisch geregelt · nur Anzeige', 'caption'))
                    self.fan_widgets[name] = (value_label, None)
                self.fan_area.addWidget(frame)
        for name, (value_label, slider) in getattr(self, 'fan_widgets', {}).items():
            current = round(fval(st.get(name, {}).get('speed')) * 100)
            if slider is None:
                value_label.setText(f'{current} %')
                continue
            pending = self._fan_pending.get(name)
            if pending:
                target, when = pending
                if abs(current - target) <= 1:
                    self._fan_pending.pop(name, None)
                elif time.monotonic() - when < 4.0:
                    value_label.setText(f'{slider.value()} %')
                    continue  # Moonraker poll hasn't caught up with latest drag value
                else:
                    self._fan_pending.pop(name, None)
            timer = self._fan_timers.get(name)
            if slider.isSliderDown() or (timer and timer.isActive()):
                value_label.setText(f'{slider.value()} %')
                continue
            slider.blockSignals(True)
            slider.setValue(current)
            slider.blockSignals(False)
            value_label.setText(f'{current} %')
            self._fan_last_sent[name] = current

    def fan_slider_changed(self, name, value):
        pair = getattr(self, 'fan_widgets', {}).get(name)
        if not pair or pair[1] is None:
            return
        pair[0].setText(f'{value} %')
        timer = self._fan_timers.get(name)
        if timer is not None and not timer.isActive():
            timer.start(140)  # sample latest slider position every ~140 ms while dragging

    def fan_slider_released(self, name):
        timer = self._fan_timers.get(name)
        if timer is not None:
            timer.stop()
        self.apply_fan(name)  # final value is sent immediately on release

    def apply_fan(self, name):
        pair = getattr(self, 'fan_widgets', {}).get(name)
        if not pair or pair[1] is None:
            return
        value = pair[1].value()
        if self._fan_last_sent.get(name) == value:
            return
        self._fan_last_sent[name] = value
        self._fan_pending[name] = (value, time.monotonic())
        if name == 'fan':
            script = f'M106 S{round(value * 255 / 100)}'
        else:
            script = f'SET_FAN_SPEED FAN={name.split(" ", 1)[1]} SPEED={value / 100:.3f}'
        # Fan controls are instant, irrespective of the general confirmation setting.
        self.network.script(script)

    def led_fade_value(self):
        return float(self.led_fade.currentText())

    def command_led_effect(self, name, mode):
        # Effect name comes from Klipper object discovery, never from text input.
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            warn(self, 'LED Effects', 'Unsupported effect name: ' + name)
            return
        fade = self.led_fade_value()
        if mode == 'stop':
            script = f'SET_LED_EFFECT EFFECT={name} STOP=1 FADETIME={fade:g}'
        elif mode == 'replace':
            script = f'SET_LED_EFFECT EFFECT={name} REPLACE=1 FADETIME={fade:g}'
        else:
            script = f'SET_LED_EFFECT EFFECT={name} FADETIME={fade:g}'
        self.confirm_script(script, 'LED effect: ' + name)

    def stop_all_led_effects(self):
        names = [x for x in self.all_objects if x.startswith('led_effect ')]
        if not names:
            return
        self.confirm_script(f'STOP_LED_EFFECTS FADETIME={self.led_fade_value():g}', 'Stop all LED effects')

    def update_led_effects(self, st):
        names = sorted(x.split(' ', 1)[1] for x in self.all_objects if x.startswith('led_effect '))
        signature = tuple(names)
        if signature != self._last_led_signature:
            self._last_led_signature = signature
            self.led_widgets.clear()
            while self.led_area.count():
                child = self.led_area.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
                elif child.layout():
                    while child.layout().count():
                        item = child.layout().takeAt(0)
                        if item.widget(): item.widget().deleteLater()
            for name in names:
                frame, c = card(name)
                status = label('—', 'caption')
                c.addWidget(status)
                c.addLayout(row(button('Activate', partial(self.command_led_effect, name, 'activate')),
                                button('Replace', partial(self.command_led_effect, name, 'replace'), True),
                                button('Stop', partial(self.command_led_effect, name, 'stop'))))
                self.led_area.addWidget(frame)
                self.led_widgets[name] = status
        self.led_status_label.setText(f'{len(names)} LED effect(s) detected' if names
                                      else 'No [led_effect ...] sections detected in Klipper.')
        for name, label_widget in self.led_widgets.items():
            active = st.get('led_effect ' + name, {}).get('enabled', False)
            label_widget.setText('● Active' if active else '○ Inactive')
            label_widget.setStyleSheet('color: #59d9a1;' if active else 'color: #9eadc2;')

    def jog(self, axis, direction):
        if self.s.get('print_stats', {}).get('state') in ('printing', 'paused'):
            warn(self, 'Achsen', 'Bewegung während Druck/Pause gesperrt.')
            return
        if axis.lower() not in self.s.get('toolhead', {}).get('homed_axes', ''):
            warn(self, 'Achsen', 'Achse ist nicht referenziert.')
            return
        dist = fval(self.jog_step.currentText()) * direction
        feed = 600 if axis == 'Z' else 6000
        self.network.script(f'SAVE_GCODE_STATE NAME=VELOX_JOG\nG91\nG1 {axis}{dist:g} F{feed}\nRESTORE_GCODE_STATE NAME=VELOX_JOG')
        self.log(f'>> Jog {axis} {dist:+g} mm')

    def update_calib(self, st):
        mesh = st.get('bed_mesh') or {}
        raw = mesh.get('probed_matrix') or []
        matrix = parse_mesh(raw)
        if not matrix:
            self.mesh_heatmap.set_matrix([])
            self.set_text('mesh', 'No active bed mesh with measured values.')
            return
        vals = [v for rr in matrix for v in rr]
        mn, mx = min(vals), max(vals)
        name = mesh.get('profile_name') or 'Active'
        self.set_text('mesh', f'Profile: {name}   ·   Points: {len(vals)} ({len(matrix[0])} × {len(matrix)})\nMin: {mn:+.3f} mm   ·   Max: {mx:+.3f} mm   ·   Δ Z: {mx-mn:.3f} mm')
        self.mesh_heatmap.set_matrix(matrix)

    def mesh_point_selected(self, col, row, value):
        self.mesh_cell_info.setText(f'X index: {col + 1}   ·   Y index: {row + 1}   ·   Z: {value:+.4f} mm')

    def print_selected(self):
        filename = self.files_list.currentText()
        if not filename:
            return
        self.confirm_script('SDCARD_PRINT_FILE FILENAME="' + filename.replace('"','') + '"', 'Druck starten: '+filename)

    def send_console(self):
        cmd = self.console_input.text().strip()
        if cmd:
            self.confirm_script(cmd, 'G-Code senden')
            self.console_input.clear()

    def confirm_script(self, script, title):
        if self.confirm_enabled:
            if QMessageBox.question(self, tr(title), tr(f'{title} ausführen?\n\n{script[:500]}'), QMessageBox.Yes|QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
        self.log('>> ' + script.replace('\n',' | '))
        self.network.script(script)
        return True


if __name__ == '__main__':
    app = QApplication(sys.argv)
    screen = app.primaryScreen()
    geometry = screen.availableGeometry() if screen is not None else None
    _, _, scale = display_profile(geometry.width() if geometry is not None else 800,
                                  geometry.height() if geometry is not None else 480,
                                  conf('display', 'ui_scale', 'auto'))
    app.setFont(QFont('DejaVu Sans', max(9, round(int(conf('appearance', 'font_size', '12')) * scale))))
    win = Screen()
    if '--windowed' in sys.argv:
        win.show()
    else:
        win.showFullScreen()
    # Start the overlay only once the native window has appeared on the display.
    QTimer.singleShot(0, win.show_startup_splash)
    sys.exit(app.exec_())
