#!/usr/bin/env python3
"""VeloxScreen native Qt Widgets alpha for Debian 12 / Raspberry Pi 4."""
import configparser
from collections import deque
import json
import math
import os
import tempfile
import re
import sys
import time
import threading
import datetime
import posixpath
import shutil
import subprocess
import wifi_manager
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from functools import partial

from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal, QRect, QRectF, QPointF, QEvent, QEasingCurve, QPropertyAnimation
from PyQt5.QtGui import QFont, QPainter, QColor, QPen, QPixmap, QLinearGradient, QBrush, QCursor, QPolygonF
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QGridLayout, QStackedWidget, QComboBox, QDoubleSpinBox, QCheckBox,
    QLineEdit, QPlainTextEdit, QMessageBox, QInputDialog, QSplitter, QSizePolicy, QDialog, QScroller, QScrollerProperties, QSlider,
    QGraphicsOpacityEffect, QListWidget, QListWidgetItem, QAbstractItemView, QProgressBar, QButtonGroup
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


def read_layout_order(key):
    """Accept old comma-separated fan_order and lossless JSON lists."""
    raw = conf('layout', key, '').strip()
    if raw.startswith('['):
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                return list(dict.fromkeys(v for v in values if isinstance(v, str) and v.strip()))
        except (ValueError, TypeError):
            pass
    return list(dict.fromkeys(v.strip() for v in raw.split(',') if v.strip()))


def ordered_items(defaults, preferred):
    """Ignore missing IDs, deduplicate, append newly discovered items."""
    defaults = list(dict.fromkeys(defaults))
    allowed = set(defaults)
    return list(dict.fromkeys([v for v in preferred if v in allowed] + defaults))


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


def heater_state(data):
    """Classify live temperature versus target, not instantaneous PWM output."""
    target = fval(data.get('target'), float('nan'))
    actual = fval(data.get('temperature'), float('nan'))
    if not math.isfinite(target) or target < 0:
        return 'unknown'
    if target == 0:
        return 'off'
    if not math.isfinite(actual):
        return 'unknown'
    if actual < target - 2.0:
        return 'heating'
    if actual > target + 2.0:
        return 'cooling'
    return 'ready'


def matching_preset_state(targets, status):
    """A preset is active only when all applicable targets are confirmed live."""
    if not targets or not any(value > 0 for value in targets.values()):
        return 'off'
    states = []
    for heater, expected in targets.items():
        entry = status.get(heater, {})
        actual_target = fval(entry.get('target'), float('nan'))
        if not math.isfinite(actual_target) or abs(actual_target - expected) > 0.01:
            return 'off'
        state = heater_state(entry)
        if state == 'unknown':
            return 'off'
        states.append(state)
    if 'heating' in states:
        return 'heating'
    if 'cooling' in states:
        return 'cooling'
    return 'ready'


def host_shutdown_allowed(status):
    """Only permit an explicitly known idle/non-printing virtual SD state."""
    state = status.get('print_stats', {}).get('state')
    active = status.get('virtual_sdcard', {}).get('is_active')
    return state in ('standby', 'complete', 'cancelled', 'error') and active is False


def toolhead_coordinates(status):
    """Commanded G-code and machine coordinates, not physical encoder feedback."""
    gm = status.get('gcode_move', {})
    th = status.get('toolhead', {})
    def coordinate(values, index):
        if not isinstance(values, (tuple, list)) or len(values) <= index:
            return None
        value = fval(values[index], float('nan'))
        return value if math.isfinite(value) else None
    machine = gm.get('position', th.get('position'))
    homed = th.get('homed_axes', '')
    return {axis: (coordinate(gm.get('gcode_position'), i), coordinate(machine, i),
                   axis.lower() in homed if isinstance(homed, str) else False)
            for i, axis in enumerate('XYZ')}


def jog_step_values(values):
    """Keep configured steps, always offer the requested 50 mm startup default."""
    return sorted({float(v) for v in values if math.isfinite(v) and v > 0} | {50.0})


def homing_axes(script):
    return {'G28': 'xyz', 'G28 X Y': 'xy', 'G28 X': 'x',
            'G28 Y': 'y', 'G28 Z': 'z'}.get(script, '')


def homing_visual_state(script, status, ready, busy):
    axes = homing_axes(script)
    homed = status.get('toolhead', {}).get('homed_axes', '')
    return 'homed' if ready and not busy and axes and isinstance(homed, str) and all(a in homed for a in axes) else 'neutral'


def manual_motion_allowed(status, axes=''):
    homed = status.get('toolhead', {}).get('homed_axes', '')
    return (host_shutdown_allowed(status)
            and status.get('pause_resume', {}).get('is_paused') is not True
            and isinstance(homed, str) and all(a in homed for a in axes))


Z_STEPS = (.005, .01, .025, .05)


def live_z_value(status):
    origin = status.get('gcode_move', {}).get('homing_origin')
    if isinstance(origin, (list, tuple)) and len(origin) >= 3:
        z = fval(origin[2], float('nan'))
        if math.isfinite(z):
            return z
    return None


def z_single_tool(status):
    cfg = status.get('configfile', {}).get('settings', {})
    return ('extruder' in cfg and status.get('toolhead', {}).get('extruder') == 'extruder'
            and not any(re.fullmatch(r'extruder[1-9]\d*', k) or k.startswith(('dual_carriage', 'tool ', 'toolchanger')) for k in cfg))


def z_save_method(status, eddy_method=''):
    """Fail closed for ambiguous tool offsets, homing configurations and overridden commands."""
    cfg = status.get('configfile', {}).get('settings', {})
    if not z_single_tool(status):
        return None, 'Multi-tool or unknown tool setup: use your tool-specific calibration workflow.'
    if any(k.lower() in ('gcode_macro z_offset_apply_probe', 'gcode_macro z_offset_apply_endstop', 'gcode_macro save_config') for k in cfg):
        return None, 'Custom offset/save macros detected. Use the printer-specific workflow.'
    zcfg = cfg.get('stepper_z', {})
    pin = str(zcfg.get('endstop_pin') or '').strip().lstrip('^~!')
    if not pin:
        return None, 'Unknown Z endstop configuration.'
    if ':' in pin and pin.endswith('virtual_endstop'):
        if pin != 'probe:z_virtual_endstop':
            return None, 'Unknown virtual Z endstop.'
        eddy = [k for k in cfg if k.startswith('probe_eddy_current ')]
        if eddy:
            if len(eddy) != 1 or eddy_method not in ('scan', 'tap') or any(k in cfg for k in ('probe', 'bltouch', 'cartographer', 'scanner')):
                return None, 'Eddy: select scan or tap in the babystep configuration after verifying your firmware.'
            return ('Z_OFFSET_APPLY_PROBE METHOD=tap' if eddy_method == 'tap' else 'Z_OFFSET_APPLY_PROBE'), 'Eddy ' + eddy_method
        known = [k for k in ('probe', 'bltouch', 'cartographer', 'scanner', 'smart_effector') if k in cfg]
        if len(known) != 1 or known[0] == 'scanner':
            return None, 'Probe calibration method not unambiguously recognized.'
        if known[0] == 'cartographer' and str(cfg['cartographer'].get('register_as_probe', True)).lower() in ('false','0','no'):
            return None, 'Cartographer is not registered as the Z probe.'
        return 'Z_OFFSET_APPLY_PROBE', known[0]
    if 'virtual' in pin.lower() or not math.isfinite(fval(zcfg.get('position_endstop'), float('nan'))):
        return None, 'Unknown physical Z endstop calibration.'
    if 'homing_override' in cfg:
        return None, 'Custom Z homing: verify the printer-specific calibration method.'
    return 'Z_OFFSET_APPLY_ENDSTOP', 'Physical Z endstop'


def z_save_snapshot(status, eddy_method=''):
    z = live_z_value(status)
    command, reason = z_save_method(status, eddy_method)
    if not command:
        return None, reason
    if z is None or abs(z) < .0005 or abs(z) > .5:
        return None, 'A nonzero live Z offset of at most 0.5 mm is required.'
    if status.get('configfile', {}).get('save_config_pending') is not False:
        return None, 'Existing or unknown pending SAVE_CONFIG changes. Resolve them first.'
    return {'z': z, 'command': command, 'method': reason,
            'filename': status.get('print_stats', {}).get('filename'),
            'tool': status.get('toolhead', {}).get('extruder'),
            'duration': fval(status.get('print_stats', {}).get('total_duration'), 0)}, ''


def z_snapshot_matches(a, b):
    return bool(a and b and all(a.get(k) == b.get(k) for k in ('command','method','filename','tool'))
                and abs(a['z'] - b['z']) < .0001)


def bounded_number(value, default, minimum, maximum):
    v = fval(value, default)
    return max(minimum, min(maximum, v if math.isfinite(v) else default))


def flag(section, key, default=True):
    return conf(section, key, str(default)).strip().lower() in ('true', 'yes', '1', 'on')


def print_progress(status):
    raw = status.get('display_status', {}).get('progress')
    if raw is None or not math.isfinite(fval(raw, float('nan'))):
        raw = status.get('virtual_sdcard', {}).get('progress')
    return bounded_number(raw, 0, 0, 1)


def remaining_seconds(progress, elapsed, estimated):
    """Blend initial slicer estimate and observed progress. Not a completion guarantee."""
    progress = bounded_number(progress, 0, 0, 1)
    elapsed = bounded_number(elapsed, 0, 0, 1e9)
    estimated = bounded_number(estimated, 0, 0, 1e9)
    if progress >= 1:
        return 0.0
    model = estimated * (1 - progress) if estimated > 0 else None
    observed = elapsed / progress * (1 - progress) if progress >= .02 and elapsed >= 10 else None
    if observed is None:
        return model
    if model is None:
        return observed
    weight = min(1.0, max(0.0, (progress - .02) / .28))
    return model * (1 - weight) + observed * weight


def duration_text(seconds):
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return '—'
    m = int(math.ceil(seconds / 60))
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f'{d}d {h:02d}h {m:02d}m'
    return f'{h:02d}h {m:02d}m'


def filament_grams(length_mm, diameter, density):
    if not all(math.isfinite(v) and v > 0 for v in (diameter, density)) or not math.isfinite(length_mm) or length_mm < 0:
        return None
    return length_mm * math.pi * (diameter / 2)**2 / 1000 * density


def safe_thumbnail_path(filename, relative):
    if not isinstance(relative, str) or not relative or relative.startswith('/') or '\\' in relative:
        return None
    if any(x == '..' for x in relative.split('/')):
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(filename), relative))
    if path.startswith(('/', '../')) or path == '..':
        return None
    return '/server/files/gcodes/' + urllib.parse.quote(path, safe='/')


def valid_print_path(path):
    return (isinstance(path, str) and bool(path) and not path.startswith('/')
            and not any(c in path for c in ('\\', '\r', '\n', '\x00'))
            and all(part not in ('', '.', '..') for part in path.split('/'))
            and path.lower().endswith(('.gcode', '.gco')))


def print_file_entries(entries):
    if not isinstance(entries, list):
        return []
    unique = {}
    for item in entries:
        if isinstance(item, dict) and valid_print_path(item.get('path')):
            unique[item['path']] = item
    return sorted(unique.values(), key=lambda item: (fval(item.get('modified'), 0), item['path']), reverse=True)


def exclude_objects(status):
    obj = status.get('exclude_object')
    if not isinstance(obj, dict) or not isinstance(obj.get('objects'), list):
        return []
    excluded = obj.get('excluded_objects', [])
    if not isinstance(excluded, list):
        return []
    names = dict.fromkeys(o['name'] for o in obj['objects'] if isinstance(o, dict)
                          and isinstance(o.get('name'), str) and o['name'])
    return [(name, name in excluded, name == obj.get('current_object')) for name in names]


def valid_exclude_name(name):
    # Only single-token names can safely be passed to Klipper extended G-code.
    return isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.:+-]+', name) is not None


def exclusion_allowed(status, filename, name, duration):
    ps = status.get('print_stats', {})
    now = fval(ps.get('total_duration'), float('nan'))
    return (bool(filename) and ps.get('filename') == filename
            and ps.get('state') in ('printing', 'paused')
            and math.isfinite(now) and math.isfinite(duration) and now >= duration
            and valid_exclude_name(name)
            and any(n == name and not excluded for n, excluded, _ in exclude_objects(status)))


def object_xy(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (ValueError, TypeError, OverflowError):
        return None
    return (x, y) if all(math.isfinite(v) and abs(v) <= 100000 for v in (x, y)) else None


def object_shapes(status):
    """Keep real Klipper polygons; never invent the footprint of a missing object."""
    entries = {n: (excluded, current) for n, excluded, current in exclude_objects(status)}
    shapes = []
    seen = set()
    for item in status.get('exclude_object', {}).get('objects', []) if entries else []:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        if not isinstance(name, str) or name not in entries or name in seen:
            continue
        seen.add(name)
        raw = item.get('polygon')
        poly = tuple(object_xy(pt) for pt in raw) if isinstance(raw, (list, tuple)) and 3 <= len(raw) <= 4096 else ()
        if not poly or None in poly or abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(poly, poly[1:]+poly[:1]))) < 1e-8:
            poly = ()
        center = object_xy(item.get('center'))
        if center is None and poly:
            center = (sum(x for x,y in poly)/len(poly), sum(y for x,y in poly)/len(poly))
        if poly or center is not None:
            shapes.append((name, poly, center, *entries[name]))
    return tuple(shapes)


def object_map_bounds(status, shapes, fit=False):
    toolhead = status.get('toolhead', {})
    low, high = object_xy(toolhead.get('axis_minimum')), object_xy(toolhead.get('axis_maximum'))
    known = not fit and low is not None and high is not None and high[0]>low[0] and high[1]>low[1]
    points = [pt for _,poly,center,_,_ in shapes for pt in (poly or (center,))]
    if known:
        points += [low, high]
    if not points:
        return (0., 0., 1., 1.), False
    x0,y0 = min(x for x,y in points), min(y for x,y in points)
    x1,y1 = max(x for x,y in points), max(y for x,y in points)
    # Include all polygons even when configured travel limits are smaller.
    if not known or x1-x0 < 1 or y1-y0 < 1:
        pad = max(5., .05*max(x1-x0,y1-y0))
        x0,y0,x1,y1 = x0-pad,y0-pad,x1+pad,y1+pad
    return (x0,y0,x1,y1), known


def object_map_projection(bounds, width, height):
    x0,y0,x1,y1 = bounds
    scale = min(max(1.,width-32)/(x1-x0), max(1.,height-32)/(y1-y0))
    return scale, (width-(x1-x0)*scale)/2-x0*scale, (height+(y1-y0)*scale)/2+y0*scale


def object_map_point(point, projection):
    scale, ox, oy = projection
    return (ox+point[0]*scale, oy-point[1]*scale)


def object_polygon_contains(point, poly):
    # Even/odd rule, including a tap directly on an edge.
    x,y = point
    inside = False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        ax,ay = a; bx,by = b
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross)<1e-7 and min(ax,bx)-1e-7<=x<=max(ax,bx)+1e-7 and min(ay,by)-1e-7<=y<=max(ay,by)+1e-7:
            return True
        if (ay>y)!=(by>y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def object_map_hits(shapes, projection, point):
    # Do not silently select one of multiple overlapping objects.
    # Excluded shapes still count as overlaps, but cannot be selected.
    hits = []
    for name,poly,center,excluded,current in shapes:
        pixels = tuple(object_map_point(pt, projection) for pt in poly)
        hit = bool(pixels) and object_polygon_contains(point,pixels)
        if not pixels and center is not None:
            cx,cy = object_map_point(center,projection)
            hit = (point[0]-cx)**2+(point[1]-cy)**2 <= 12**2
        if hit:
            hits.append(name)
    return hits


def display_style(style, scale):
    out = scaled_style(style, scale * bounded_number(conf('display', 'font_scale', '1'), 1, .8, 1.5))
    factor = bounded_number(conf('display', 'button_scale', '1'), 1, .8, 1.5)
    # Preserve the emergency button minimum size. Other explicit widget sizes remain valid.
    out += f'\nQPushButton {{ min-height: {round(25*factor)}px; padding: {round(6*factor)}px {round(9*factor)}px; }}'
    out += f'\nQPushButton#nav {{ min-height: {round(29*factor)}px; padding: {round(9*factor)}px 7px; }}'
    return out



class Network(QObject):
    data = pyqtSignal(object)
    err = pyqtSignal(str)
    reply = pyqtSignal(str)
    gcode_messages = pyqtSignal(object)
    emergency_triggered = pyqtSignal()
    printer_health = pyqtSignal(object)
    recovery_result = pyqtSignal(str)
    shutdown_result = pyqtSignal(bool, str)
    print_metadata = pyqtSignal(str, object, object)
    print_action_result = pyqtSignal(str, bool, str)
    live_z_result = pyqtSignal(bool, str)
    z_save_result = pyqtSignal(bool, str)
    motion_result = pyqtSignal(str, bool, str, bool)
    files_result = pyqtSignal(object, str)
    file_preview = pyqtSignal(int, str, object, object)
    file_start_result = pyqtSignal(bool, str)
    exclude_result = pyqtSignal(bool, str)

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
        self._preview_cv = threading.Condition()
        self._preview_batch = None
        self._preview_generation = 0
        threading.Thread(target=self._file_preview_worker, daemon=True).start()
        threading.Thread(target=self._script_worker, daemon=True).start()

    def call(self, path, method='GET', payload=None, timeout=3):
        url = self.url + path
        body = urllib.parse.urlencode(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8')
            return json.loads(raw) if raw else {}

    def poll(self):
        if self._running:
            return
        self._running = True
        poll_started = time.monotonic()

        def run():
            try:
                # Server info remains available even when Klippy cannot answer object queries.
                try:
                    info = self.call('/server/info').get('result', {})
                except Exception as exc:
                    self.printer_health.emit({'state': 'offline', 'message': str(exc)})
                    return
                state = str(info.get('klippy_state') or 'unknown').lower()
                message = ''
                if state != 'disconnected':
                    try:
                        detail = self.call('/printer/info').get('result', {})
                        state = str(detail.get('state') or state).lower()
                        message = str(detail.get('state_message') or '')
                    except Exception:
                        pass  # Server state is still useful if Klippy is unreachable.
                self.printer_health.emit({'state': state, 'message': message})
                if state != 'ready':
                    self.objects = []
                    return
                if not self.objects or self._tick % 30 == 0:
                    discovered = self.call('/printer/objects/list')
                    self.objects = discovered.get('result', {}).get('objects', [])
                requested = [x for x in self.objects if x in (
                    'toolhead', 'motion_report', 'gcode_move', 'print_stats',
                    'virtual_sdcard', 'heaters', 'bed_mesh', 'exclude_object',
                    'firmware_retraction', 'configfile', 'display_status', 'pause_resume')
                    or x == 'extruder' or re.fullmatch(r'extruder\d+', x)
                    or x == 'heater_bed' or x.startswith(('temperature_sensor ', 'heater_generic ',
                                                          'temperature_fan ', 'fan_generic ', 'heater_fan '))
                    or x == 'fan' or x.startswith(('led_effect ', 'filament_switch_sensor ', 'filament_motion_sensor '))]
                query = '&'.join(urllib.parse.quote(o, safe='') for o in requested)
                payload = self.call('/printer/objects/query?' + query)
                self._tick += 1
                self.data.emit({'objects': self.objects,
                                'status': payload.get('result', {}).get('status', {}),
                                'timestamp': payload.get('result', {}).get('eventtime', 0),
                                'poll_started': poll_started})
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

    def get_file_preview(self, filename):
        metadata, thumbnail = {}, b''
        try:
            metadata = self.call('/server/files/metadata?' + urllib.parse.urlencode({'filename': filename})).get('result', {})
            if not isinstance(metadata, dict):
                metadata = {}
            choices = [item for item in metadata.get('thumbnails', []) if isinstance(item, dict)]
            choices.sort(key=lambda item: abs(fval(item.get('width'), 200) - 240))
            for item in choices:
                path = safe_thumbnail_path(filename, item.get('relative_path'))
                if not path:
                    continue
                try:
                    with urllib.request.urlopen(self.url + path, timeout=3) as response:
                        data = response.read(2 * 1024 * 1024 + 1)
                    if 0 < len(data) <= 2 * 1024 * 1024:
                        thumbnail = data
                        break
                except Exception:
                    pass
        except Exception:
            pass
        return metadata, thumbnail

    def fetch_print_metadata(self, filename):
        def work():
            metadata, thumbnail = self.get_file_preview(filename)
            self.print_metadata.emit(filename, metadata, thumbnail)
        threading.Thread(target=work, daemon=True).start()

    def request_file_previews(self, generation, filenames):
        # One serial worker, latest visible page only. No thread per file.
        with self._preview_cv:
            self._preview_generation = generation
            self._preview_batch = (generation, list(filenames))
            self._preview_cv.notify()

    def _file_preview_worker(self):
        while True:
            with self._preview_cv:
                while self._preview_batch is None:
                    self._preview_cv.wait()
                generation, filenames = self._preview_batch
                self._preview_batch = None
            for filename in filenames:
                with self._preview_cv:
                    if generation != self._preview_generation:
                        break
                metadata, thumbnail = self.get_file_preview(filename)
                self.file_preview.emit(generation, filename, metadata, thumbnail)

    def start_file(self, filename):
        def work():
            try:
                if not valid_print_path(filename):
                    raise RuntimeError('Invalid print file path.')
                if self.call('/printer/info').get('result', {}).get('state') != 'ready':
                    raise RuntimeError('Klipper is not ready.')
                query = '/printer/objects/query?print_stats&virtual_sdcard'
                if 'pause_resume' in self.objects:
                    query += '&pause_resume'
                status = self.call(query).get('result', {}).get('status', {})
                if not manual_motion_allowed(status):
                    raise RuntimeError('Printer is no longer idle.')
                self.call('/printer/print/start', 'POST', {'filename': filename})
                self.file_start_result.emit(True, 'Print start accepted.')
            except Exception as exc:
                self.file_start_result.emit(False, 'Print start not confirmed; check printer status before retrying: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def exclude_print_object(self, filename, name, duration):
        def work():
            try:
                if self.call('/printer/info').get('result', {}).get('state') != 'ready':
                    raise RuntimeError('Klipper is not ready.')
                status = self.call('/printer/objects/query?print_stats&exclude_object').get('result', {}).get('status', {})
                if not exclusion_allowed(status, filename, name, duration):
                    raise RuntimeError('Print or object state changed; no exclusion sent.')
                self.call('/printer/gcode/script', 'POST', {'script': 'EXCLUDE_OBJECT NAME=' + name})
                self.exclude_result.emit(True, 'Object exclusion requested: ' + name)
            except Exception as exc:
                self.exclude_result.emit(False, 'Exclusion not confirmed; check object status: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def print_action(self, action):
        """Separate confirmed user actions from the generic command queue."""
        valid = {'pause': ('printing',), 'resume': ('paused',), 'cancel': ('printing', 'paused')}
        if action not in valid:
            return
        def work():
            try:
                status = self.call('/printer/objects/query?print_stats').get('result', {}).get('status', {})
                if status.get('print_stats', {}).get('state') not in valid[action]:
                    raise RuntimeError('Print state changed; action not sent.')
                self.call('/printer/print/' + action, 'POST')
                self.print_action_result.emit(action, True, action.capitalize() + ' requested.')
            except Exception as exc:
                self.print_action_result.emit(action, False, str(exc))
        threading.Thread(target=work, daemon=True).start()


    def adjust_live_z(self, step, reset_expected=None):
        if reset_expected is None and abs(step) not in Z_STEPS:
            return
        def work():
            try:
                status = self.call('/printer/objects/query?print_stats&toolhead&gcode_move&configfile').get('result', {}).get('status', {})
                if status.get('print_stats', {}).get('state') != 'printing' or 'z' not in status.get('toolhead', {}).get('homed_axes', ''):
                    raise RuntimeError('Printer is no longer actively printing with a homed Z axis.')
                if reset_expected is not None:
                    actual = live_z_value(status)
                    if not z_single_tool(status) or actual is None or abs(actual) > .5 or abs(actual-reset_expected) >= .0001:
                        raise RuntimeError('Z offset or tool setup changed; reset cancelled.')
                    script = 'SET_GCODE_OFFSET Z=0 MOVE=1'
                else:
                    script = f'SET_GCODE_OFFSET Z_ADJUST={step:+.3f}'.rstrip('0').rstrip('.') + ' MOVE=1'
                self.call('/printer/gcode/script', 'POST', {'script': script})
                self.live_z_result.emit(True, 'Live Z adjustment accepted.')
            except Exception as exc:
                self.live_z_result.emit(False, 'Z adjustment not confirmed: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def save_live_z(self, expected, eddy_method=''):
        """Recheck the live offset, method and idle status. Never auto-retry a save."""
        def work():
            attempted = False
            try:
                commands = self.call('/printer/gcode/help').get('result', {})
                if expected['command'].split()[0] not in commands or 'SAVE_CONFIG' not in commands:
                    raise RuntimeError('Required calibration commands are not advertised by Klipper.')
                health = self.call('/printer/info').get('result', {})
                if health.get('state') != 'ready':
                    raise RuntimeError('Klipper is not ready.')
                status = self.call('/printer/objects/query?print_stats&virtual_sdcard&toolhead&gcode_move&configfile&pause_resume').get('result', {}).get('status', {})
                if (status.get('print_stats', {}).get('state') not in ('standby', 'complete')
                        or not host_shutdown_allowed(status) or status.get('pause_resume', {}).get('is_paused') is True):
                    raise RuntimeError('Printer is not confirmed idle after a successful print or in standby.')
                current, reason = z_save_snapshot(status, eddy_method)
                if not z_snapshot_matches(expected, current):
                    raise RuntimeError(reason or 'Live offset or calibration method changed. Review again.')
                attempted = True
                self.call('/printer/gcode/script', 'POST', {'script': expected['command'] + '\nSAVE_CONFIG'})
                self.z_save_result.emit(True, 'Save request accepted. Verify the calibration after Klipper restarts.')
            except Exception as exc:
                detail = ('Save result uncertain; check printer.cfg / console before retrying: ' if attempted else 'Save blocked: ') + str(exc)
                self.z_save_result.emit(False, detail)
        threading.Thread(target=work, daemon=True).start()

    def emergency_stop(self):
        """Moonraker's dedicated emergency stop endpoint; never wait in the G-Code FIFO."""
        def work():
            try:
                self.call('/printer/emergency_stop', method='POST')
                self.reply.emit('Emergency stop sent to Moonraker.')
                self.emergency_triggered.emit()
            except Exception as exc:
                self.err.emit('Emergency stop request failed: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def recovery_action(self, action):
        """Use direct Moonraker endpoints, never the G-Code FIFO after an M112."""
        endpoints = {
            'klipper': ('/printer/restart', 'Klipper restart requested.'),
            'firmware': ('/printer/firmware_restart', 'Firmware restart requested.'),
            'host': ('/machine/reboot', 'Host reboot requested.'),
        }
        if action not in endpoints:
            self.err.emit('Unknown recovery action.')
            return
        path, message = endpoints[action]
        def work():
            try:
                self.call(path, method='POST')
                self.reply.emit(message)
                self.recovery_result.emit(message + ' Waiting for printer status...')
            except Exception as exc:
                # The host may deliberately disconnect Moonraker during a reboot.
                # A failed request is not automatically proof of a successful restart.
                self.recovery_result.emit('Recovery request status unknown: ' + str(exc))
                self.err.emit('Recovery request status unknown: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def shutdown_host(self):
        """Request a graceful OS shutdown, after a fresh printer-state check."""
        def work():
            try:
                status = self.call('/printer/objects/query?print_stats&virtual_sdcard').get('result', {}).get('status', {})
                if not host_shutdown_allowed(status):
                    self.shutdown_result.emit(False, 'Shutdown blocked: printer is not confirmed idle.')
                    return
            except Exception as exc:
                self.shutdown_result.emit(False, 'Cannot verify printer status: ' + str(exc))
                return
            try:
                self.call('/machine/shutdown', method='POST')
                self.shutdown_result.emit(True, 'Host shutdown requested.')
            except urllib.error.HTTPError as exc:
                self.shutdown_result.emit(False, 'Shutdown rejected by Moonraker: HTTP ' + str(exc.code))
            except Exception as exc:
                # The server may disappear before sending its response. Do not retry automatically.
                self.shutdown_result.emit(False, 'Shutdown request status unknown: ' + str(exc))
        threading.Thread(target=work, daemon=True).start()

    def script(self, script):
        # A single FIFO worker preserves G-Code ordering, including fan slider moves.
        with self._script_cv:
            self._script_queue.append(script)
            # Commands are absolute targets: when tapped rapidly, only the
            # latest queued fan/M220/M221 target for that channel is needed.
            def coalesce_key(command):
                if not isinstance(command, str):
                    return None
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

    def manual_motion(self, script, axes='', required_object='', tracked=False):
        # Same FIFO as other G-code. Recheck immediately before dispatch, not just on tap.
        with self._script_cv:
            self._script_queue.append((script, axes, required_object, tracked))
            self._script_cv.notify()

    def run_manual_motion(self, script, axes, required_object):
        health = self.call('/printer/info').get('result', {})
        if health.get('state') != 'ready':
            raise RuntimeError('Klipper is not ready; motion blocked.')
        if required_object:
            objects = self.call('/printer/objects/list').get('result', {}).get('objects', [])
            if required_object not in objects:
                raise RuntimeError('Calibration feature is no longer available.')
        query = '/printer/objects/query?print_stats&virtual_sdcard&toolhead'
        if 'pause_resume' in self.objects:
            query += '&pause_resume'
        status = self.call(query).get('result', {}).get('status', {})
        if not manual_motion_allowed(status, axes):
            raise RuntimeError('Manual motion blocked: printer is not confirmed idle or required axes are not homed.')
        # Homing and gantry calibration can take longer than the normal HTTP timeout.
        # Never retry a timed-out command automatically.
        return self.call('/printer/gcode/script', 'POST', {'script': script}, timeout=120)

    def _script_worker(self):
        while True:
            with self._script_cv:
                while not self._script_queue:
                    self._script_cv.wait()
                script = self._script_queue.pop(0)
            tracked = False
            try:
                if isinstance(script, tuple):
                    script, axes, required_object, tracked = script
                    # Await the final queued moves too (custom homing/calibration macros).
                    command = script + '\nM400' if tracked and script != 'M84' else script
                    response = self.run_manual_motion(command, axes, required_object)
                    if tracked:
                        self.motion_result.emit(script, True, '', False)
                else:
                    response = self.call('/printer/gcode/script', 'POST', {'script': script})
                # Do not flood the console with every intermediate slider value.
                if not script.startswith(('M106 S', 'SET_FAN_SPEED FAN=')):
                    self.reply.emit('OK  ' + script.replace('\n', ' | ') + '  ' + str(response.get('result', '')))
            except Exception as exc:
                if tracked:
                    uncertain = isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)) and not isinstance(exc, urllib.error.HTTPError)
                    self.motion_result.emit(script, False, str(exc), uncertain)
                else:
                    self.err.emit('G-Code: ' + str(exc))

    def load_files(self, success):
        # Callbacks are only invoked via a Qt signal from worker to GUI.
        def run():
            try:
                result = self.call('/server/files/list?root=gcodes')
                self.files_result.emit(result.get('result', []), '')
            except Exception as exc:
                self.files_result.emit([], str(exc))
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
    ('Dateien', 'Print Files'),
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
    ('Print Files', 'Druckdateien'),
    ('Exclude Object', 'Objekt ausschließen'),
    ('Bed view', 'Bettansicht'), ('Object list', 'Objektliste'),
    ('Fit objects', 'Einpassen'), ('Bed / XY', 'Bett / XY'),
    ('Exclude selected', 'Ausschließen'), ('Tap to select', 'Antippen zum Auswählen'),
    ('Center markers', 'Mittelpunkte'),
    ('Exclude selected object', 'Objekt ausschließen'),
    ('No object geometry. Use List.', 'Keine Objektgeometrie. Liste nutzen.'),
    ('XY travel / object area', 'XY-Fahrbereich / Objektfläche'),
    ('Object area (auto fit)', 'Objektfläche (automatisch)'), ('mapped', 'dargestellt'),
    ('Selected: ', 'Ausgewählt: '),
    ('Tap an object · Green: printing · Red: excluded', 'Objekt antippen · Grün: Druck · Rot: ausgeschlossen'),
    ('Overlapping objects. Select the exact name in List.', 'Überlappung. Namen in Liste auswählen.'),
    ('No preview', 'Keine Vorschau'),
    ('Previous page', 'Zurück'),
    ('Next page', 'Weiter'),
    ('Start print', 'Druck starten'),
    ('Refresh files', 'Dateien aktualisieren'),
    ('Toolhead', 'Druckkopf'), ('G-code position · mm', 'G-Code-Position · mm'),
    ('Machine', 'Maschine'), ('Not homed', 'Nicht referenziert'), ('Homed', 'Referenziert'),
    ('Homing in progress', 'Referenzierung läuft'), ('Waiting for live axis status', 'Warte auf aktuelle Achsenwerte'),
    ('Check printer and restart VeloxScreen before further manual motion.', 'Drucker prüfen und VeloxScreen vor weiteren manuellen Bewegungen neu starten.'),
    ('Motion request in progress', 'Bewegungsbefehl läuft'), ('Motion not confirmed', 'Bewegungsbefehl nicht bestätigt'),
    ('Step · mm', 'Schrittweite · mm'), ('Home all', 'Alle referenzieren'), ('Home XY', 'XY referenzieren'),
    ('Manual movement unavailable', 'Manuelle Bewegung nicht verfügbar'),
    ('Manual movement requires an idle printer. Home axes before jogging.', 'Manuelle Bewegung nur im Leerlauf. Achsen vorher referenzieren.'),
    ('TIME REMAINING', 'RESTZEIT'), ('FINISH AT · EST.', 'FERTIG UM · CA.'),
    ('Display comfort', 'Display-Komfort'), ('Quick actions', 'Schnellzugriffe'),
    ('Choose quick actions', 'Schnellzugriffe wählen'), ('Notification center', 'Mitteilungszentrale'),
    ('Notifications', 'Mitteilungen'), ('Clear history', 'Verlauf löschen'),
    ('Print details / Preview', 'Druckdetails / Vorschau'), ('Print details', 'Druckdetails'),
    ('Temperature history', 'Temperaturverlauf'), ('No preview', 'Keine Vorschau'),
    ('Live Z offset', 'Live-Z-Offset'), ('Screen locked for cleaning', 'Display zur Reinigung gesperrt'),
    ('Slide to unlock →', 'Zum Entsperren wischen →'), ('Drag the handle all the way to the right.', 'Regler bis ganz nach rechts ziehen.'),
    ('Lock screen for cleaning', 'Display zur Reinigung sperren'), ('Start page', 'Startseite'),
    ('Save display settings', 'Display-Einstellungen speichern'),
    ('Keep display on while printing / paused', 'Display während Druck / Pause anlassen'),
    ('Pause', 'Pause'), ('Resume', 'Fortsetzen'), ('Cancel', 'Abbrechen'),
    ('Customize layout', 'Layout anpassen'), ('Edit layout', 'Layout bearbeiten'),
    ('Reorder blocks, navigation, fans and temperature tiles. Safety controls stay fixed.',
     'Blöcke, Navigation, Lüfter und Temperaturkacheln sortieren. Sicherheitsfunktionen bleiben fest.'),
    ('Dashboard blocks', 'Dashboard-Blöcke'), ('Print blocks', 'Druck-Blöcke'),
    ('Control blocks', 'Steuerungs-Blöcke'), ('Tools blocks', 'Werkzeug-Blöcke'),
    ('Temperature blocks', 'Temperatur-Blöcke'), ('Macro blocks', 'Makro-Blöcke'),
    ('Settings blocks', 'Einstellungs-Blöcke'), ('Navigation', 'Navigation'),
    ('Temperature tiles', 'Temperaturkacheln'), ('Macro buttons', 'Makro-Schaltflächen'),
    ('Live motion', 'Live-Bewegungswerte'), ('Motion limits / Motors Off', 'Bewegungsgrenzen / Motoren aus'),
    ('Print status', 'Druckstatus'), ('Run macros', 'Makros ausführen'),
    ('Macro visibility', 'Makro-Sichtbarkeit'), ('Heaters / Sensors', 'Heizer / Sensoren'),
    ('↑ Up', '↑ Hoch'), ('↓ Down', '↓ Runter'), ('Reset order', 'Reihenfolge zurücksetzen'),
    ('Drag mode (hold and move)', 'Ziehmodus (halten und ziehen)'), ('Save layout', 'Layout speichern'),
    ('Could not save layout:', 'Layout konnte nicht gespeichert werden:'),
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
    ('Confirm host shutdown', 'Herunterfahren bestätigen'),
    ('Shutdown Host', 'Host herunterfahren'),
    ('EMERGENCY STOP', 'NOT-AUS'),
    ('Immediately stops Klipper. Requires firmware restart/recovery.', 'Stoppt Klipper sofort. Firmware-Neustart/Wiederherstellung erforderlich.'),
    ('Pointer style (restart UI)', 'Mauszeiger (UI-Neustart)'),
    ('Small dot', 'Kleiner Punkt'),
    ('Hidden pointer', 'Zeiger ausgeblendet'),
    ('System pointer', 'System-Mauszeiger'),
    ('Shutdown blocked: printer is not confirmed idle.', 'Herunterfahren gesperrt: Drucker nicht nachweislich im Leerlauf.'),
    ('The host will shut down. Let the hotends cool first. Mainsail and SSH will disconnect. Continue?',
     'Der Host wird heruntergefahren. Hotends vorher abkühlen lassen. Mainsail und SSH werden getrennt. Fortfahren?'),
    ('Shutdown is allowed only when the printer is confirmed idle. Let the hotends cool first.',
     'Herunterfahren nur bei bestätigtem Leerlauf. Hotends vorher abkühlen lassen.'),
    ('If disabled, the Settings shutdown button sends the request without asking. Active and paused prints remain protected.',
     'Wenn deaktiviert, fährt der Button in den Einstellungen ohne Rückfrage herunter. Aktive und pausierte Drucke bleiben geschützt.'),
    ('Host shutdown requested.', 'Herunterfahren des Hosts angefordert.'),
    ('Requesting host shutdown...', 'Herunterfahren wird angefordert ...'),
    ('Cannot verify printer status:', 'Druckerstatus kann nicht geprüft werden:'),
    ('Shutdown rejected by Moonraker:', 'Herunterfahren von Moonraker abgelehnt:'),
    ('Shutdown request status unknown:', 'Status der Shutdown-Anforderung unbekannt:'),
    ('Heating to', 'Heizt auf'),
    ('At target', 'Soll erreicht'),
    ('Cooling to', 'Kühlt auf'),
    ('Heating', 'Heizt auf'),
    ('Cooling', 'Kühlt ab'),
    ('Heater off', 'Heizung aus'),
    ('Target unavailable', 'Sollwert nicht verfügbar'),
    ('Print temperatures', 'Drucktemperaturen'),
    ('Waiting for heater data...', 'Warte auf Heizungsdaten ...'),
    ('No heaters or chamber sensors found.', 'Keine Heizungen oder Kammersensoren gefunden.'),
    ('Heater temperature limit is unavailable.', 'Temperaturgrenze der Heizung nicht verfügbar.'),
    ('Sensor only', 'Nur Sensor'),
    ('Target', 'Soll'),
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
        if text == 'Dateien':
            return 'Druckdateien'
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
QWidget { color: #e8edf6; font-family: 'DejaVu Sans'; }
QWidget#veloxRoot, QWidget#veloxPage, QWidget#veloxQuickPanel, QWidget#veloxScrollViewport, QDialog { background: #0c1119; }
QLabel { background: transparent; }
QLabel#sectionTitle { font-size: 13px; font-weight: 700; }
QLabel#readout { font-size: 13px; font-weight: 600; }
QLabel#etaValue { font-size: 30px; font-weight: 750; }
QLabel#etaValueSmall { font-size: 23px; font-weight: 750; }
QLabel#metricSmall { font-size: 15px; font-weight: 750; }
QLabel#title { font-size: 18px; font-weight: 750; }
QLabel#brand { font-size: 20px; font-weight: 800; color: #f2f7ff; }
QLabel#tempValue { font-size: 17px; font-weight: 700; color: #ffffff; }
QLabel#tempName { color: #b6cbe3; font-size: 12px; font-weight: 600; }
QLabel#subtitle { font-size: 9px; font-weight: 500; color: #8fa1b9; }
QLabel#metric { font-size: 23px; font-weight: 750; color: #fbfcff; }
QLabel#caption { color: #9eadc2; font-size: 11px; }
QFrame#card { background: #171f2d; border: 1px solid #263247; border-radius: 10px; }
QPushButton { background: #26364e; border: 1px solid #354966; border-radius: 8px; padding: 6px 9px; font-weight: 600; font-size: 12px; }
QPushButton:hover { background: #344b6e; }
QPushButton:pressed { background: #5179ad; }
QPushButton:disabled { color: #65738a; background: #1a2534; }
QPushButton#primary { background: #2866bd; border: 1px solid #3377d8; }
QPushButton#nav { background: #151f2d; text-align: left; border: 0; padding: 9px 7px; font-size: 15px; min-height: 29px; }
QPushButton#danger { background: #8f2029; color: #ffffff; border: 2px solid #dd4554; font-size: 16px; font-weight: 800; min-height: 44px; }
QPushButton#danger:pressed { background: #ad2433; }
QPushButton#nav:checked { background: #244c79; border-left: 4px solid #75c3ff; }
QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit { background: #111a27; border: 1px solid #3a4c67; border-radius: 7px; padding: 7px; }
QScrollArea { border: 0; background: transparent; }
QProgressBar { border: 0; border-radius: 4px; background: #111a27; }
QProgressBar::chunk { background: #2866bd; border-radius: 4px; }
QAbstractItemView { background: #111a27; border: 1px solid #3a4c67; border-radius: 5px; }
QAbstractItemView::item { padding: 4px; }
QComboBox::drop-down { border: 0; width: 22px; }
QDoubleSpinBox QLineEdit { border: 0; padding: 0; background: transparent; }
QScrollBar:horizontal { background: #152031; height: 7px; }
QScrollBar::handle:horizontal { background: #50627d; border-radius: 3px; }
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
    style += f"\nQFrame#veloxSidebar, QWidget#veloxNavPanel, QFrame#veloxSidebar QWidget#veloxScrollViewport {{ background: {side}; }}"
    style += f"\nQFrame#card QLabel {{ color: {panel_ink}; }}"
    style += f"\nQFrame#card QLabel#caption {{ color: {rgb_mix(panel_ink, panel, 0.32)}; }}"
    style += f"\nQFrame#card QLabel#tempName {{ color: {rgb_mix(panel_ink, panel, 0.22)}; }}"
    style += f"\nQFrame#veloxSidebar QLabel {{ color: {side_ink}; }}"
    style += f"\nQFrame#veloxSidebar QLabel#subtitle {{ color: {rgb_mix(side_ink, side, 0.38)}; }}"
    style += f"\nQPushButton {{ color: {btn_ink}; }}"
    style += f"\nQPushButton#primary {{ color: {accent_ink}; }}"
    style += f"\nQPushButton#nav {{ color: {side_ink}; background: {rgb_mix(side, bg, 0.22)}; }}"
    style += f"\nQPushButton#nav:checked {{ color: {text_on(rgb_mix(side, accent, 0.58))}; }}"
    # A single intentional readout surface. Ordinary labels never paint a dark rectangle.
    # Derive it from the active theme instead of baking a second unrelated palette.
    field = rgb_mix(panel, bg, 0.65)
    if field == panel:
        field = rgb_mix(panel, '#000000' if text_on(panel) == '#17202d' else '#ffffff', 0.08)
    field_ink = text_on(field)
    edge = rgb_mix(field, field_ink, 0.17)
    for name in ('metric', 'metricSmall', 'tempValue', 'readout', 'etaValue', 'etaValueSmall'):
        selector = f'QLabel#{name}, QFrame#card QLabel#{name}'
        style += f'\n{selector} {{ background: {field}; color: {field_ink}; border: 1px solid {edge}; border-radius: 6px; padding: 3px 6px; }}'
    style += f'\nQFrame#card QLabel#etaValue, QLabel#etaValue, QFrame#card QLabel#etaValueSmall, QLabel#etaValueSmall {{ color: {rgb_mix(field_ink, accent, 0.27)}; }}'
    style += f'\nQComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QAbstractItemView {{ background: {field}; color: {field_ink}; border-color: {edge}; }}'
    style += f'\nQDoubleSpinBox QLineEdit {{ background: transparent; border: 0; padding: 0; color: {field_ink}; }}'
    style += f'\nQAbstractItemView::item:selected {{ background: {accent}; color: {accent_ink}; }}'
    style += f'\nQPushButton:disabled, QPushButton#primary:disabled {{ color: {rgb_mix(btn_ink, btn, 0.45)}; background: {rgb_mix(btn, bg, 0.50)}; border-color: {edge}; }}'
    # Emergency stop must always stay red/white regardless of custom theme.
    style += '\nQPushButton#danger { background: #8f2029; color: #ffffff; border: 2px solid #dd4554; }'
    style += '\nQPushButton#danger:hover { background: #a42a35; } QPushButton#danger:pressed { background: #b33340; }'
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
    return scaled_style(source, scale * bounded_number(conf('display', 'font_scale', '1'), 1, .8, 1.5))


def pointer_mode(value):
    value = str(value).strip().lower()
    return value if value in ('dot', 'hidden', 'system') else 'dot'


def install_pointer(app, mode):
    """Application-only cursor; no X11 cursor theme or desktop changes."""
    mode = pointer_mode(mode)
    if mode == 'system':
        return
    if mode == 'hidden':
        app.setOverrideCursor(QCursor(Qt.BlankCursor))
        return
    pix = QPixmap(13, 13)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#10141b'), 1.5))
    painter.setBrush(QColor('#f5f7fa'))
    painter.drawEllipse(QRectF(3, 3, 7, 7))
    painter.end()
    app.setOverrideCursor(QCursor(pix, 6, 6))


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
    b.setMinimumHeight(round(40 * bounded_number(conf('display', 'button_scale', '1'), 1, .8, 1.5)))
    return b


def card(title=None):
    frame = QFrame()
    frame.setObjectName('card')
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(9, 7, 9, 7)
    lay.setSpacing(5)
    if title:
        lay.addWidget(label(title, 'sectionTitle'))
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
        self.viewport().setObjectName('veloxScrollViewport')
        self.viewport().setAttribute(Qt.WA_StyledBackground, True)
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


class ObjectBedView(QWidget):
    """Plan view in printer XY coordinates (positive Y up). Selection never sends G-code."""
    picked = pyqtSignal(str)
    ambiguous = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shapes = ()
        self.bounds = (0.,0.,1.,1.)
        self.known_bounds = False
        self.selection = ''
        self.press = None
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_status(self, shapes, bounds, known):
        state = (shapes,bounds,known)
        if state != (self.shapes,self.bounds,self.known_bounds):
            self.shapes,self.bounds,self.known_bounds = state
            self.update()

    def set_selection(self, name):
        name = name or ''
        if self.selection != name:
            self.selection = name
            self.update()

    def projection(self):
        return object_map_projection(self.bounds,self.width(),self.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(THEME['background_color']))
        if not self.shapes:
            p.setPen(QColor(text_on(THEME['background_color'])))
            p.drawText(self.rect(),Qt.AlignCenter,tr('No object geometry. Use List.'))
            return
        projection = self.projection()
        x0,y0,x1,y1 = self.bounds
        left,bottom = object_map_point((x0,y0),projection)
        right,top = object_map_point((x1,y1),projection)
        bed = QRectF(left,top,right-left,bottom-top)
        p.fillRect(bed,QColor(THEME['overlay_color']))
        p.setPen(QPen(QColor(THEME['button_color']),1))
        for i in range(1,10):
            x = left+(right-left)*i/10
            y = top+(bottom-top)*i/10
            p.drawLine(QPointF(x,top),QPointF(x,bottom))
            p.drawLine(QPointF(left,y),QPointF(right,y))
        p.setPen(QPen(QColor(text_on(THEME['overlay_color'])),1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bed)
        p.drawText(QRectF(left,bottom+1,max(10.,right-left),15),Qt.AlignLeft|Qt.AlignVCenter,'X →')
        p.drawText(QRectF(0,max(0.,top-16),max(30.,left+30),15),Qt.AlignLeft|Qt.AlignVCenter,'Y ↑')
        for name,poly,center,excluded,current in sorted(self.shapes,key=lambda a:a[0]==self.selection):
            selected = name==self.selection and not excluded
            fill = '#75313a' if excluded else '#2866bd' if selected else '#286052' if current else '#52677e'
            outline = '#ff9da4' if excluded else '#9cd2ff' if selected else '#79e1b0' if current else '#b5c8de'
            p.setPen(QPen(QColor(outline),2.5 if selected else 1.5))
            p.setBrush(QBrush(QColor(fill),Qt.BDiagPattern if excluded else Qt.SolidPattern))
            if poly:
                polygon = QPolygonF([QPointF(*object_map_point(pt,projection)) for pt in poly])
                p.drawPolygon(polygon,Qt.OddEvenFill)
            elif center is not None:
                cx,cy = object_map_point(center,projection)
                p.drawEllipse(QPointF(cx,cy),8.,8.)
            if center is not None and (selected or excluded or current):
                cx,cy = object_map_point(center,projection)
                p.setPen(QColor('#ffffff'))
                p.drawText(QRectF(cx-10,cy-10,20,20),Qt.AlignCenter,'×' if excluded else '✓' if selected else '•')

    def mousePressEvent(self,event):
        self.press = event.pos() if event.button()==Qt.LeftButton else None
        event.accept()

    def mouseReleaseEvent(self,event):
        start,self.press = self.press,None
        if event.button()!=Qt.LeftButton or start is None or (event.pos()-start).manhattanLength()>14:
            return
        hits = object_map_hits(self.shapes,self.projection(),(event.x(),event.y()))
        if len(hits)>1:
            self.ambiguous.emit()
        elif hits:
            self.picked.emit(hits[0])
        else:
            self.picked.emit('')
        event.accept()


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


class LayoutOrderList(QListWidget):
    order_changed = pyqtSignal()

    def dropEvent(self, event):
        # Qt 5 can implement an internal move as insert/remove, not rowsMoved.
        super().dropEvent(event)
        if event.isAccepted():
            self.order_changed.emit()


class LayoutEditor(QDialog):
    """An explicit editing mode prevents ordinary swipes from moving controls."""
    def __init__(self, parent, catalog):
        super().__init__(parent)
        self.catalog = catalog
        self.changes = {}
        self.key = None
        self.loading = False
        self.setWindowTitle(tr('Customize layout'))
        self.setStyleSheet(touch_dialog_style(STYLE))
        fit_dialog(self, 620, 440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(3)
        self.category = QComboBox()
        self.category.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.category.setMinimumContentsLength(12)
        for key, (title, _) in catalog.items():
            self.category.addItem(tr(title), key)
        outer.addWidget(self.category)
        self.items = LayoutOrderList()
        self.items.setMinimumHeight(30)
        self.items.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.items.setSpacing(1)
        self.items.setStyleSheet('QListWidget::item { padding: 5px; } '
                                 'QListWidget::item:selected { background: ' + parent.theme['accent_color'] +
                                 '; color: ' + text_on(parent.theme['accent_color']) + '; }' )
        outer.addWidget(self.items, 1)
        moves = QHBoxLayout()
        self.up = button('↑ Up', partial(self.move_item, -1))
        self.down = button('↓ Down', partial(self.move_item, 1))
        self.reset = button('Reset order', self.reset_order)
        for b in (self.up, self.down, self.reset):
            b.setMinimumHeight(24 if parent.tiny else 36)
            moves.addWidget(b)
        outer.addLayout(moves)
        self.drag = TouchCheckBox(tr('Drag mode (hold and move)'))
        self.drag.setMinimumHeight(28 if parent.tiny else 34)
        outer.addWidget(self.drag)
        self.drag.toggled.connect(self.set_drag_mode)
        actions = QHBoxLayout()
        for text, callback, primary in (('Cancel', self.reject, False), ('Save layout', self.accept, True)):
            b = button(text, callback, primary)
            b.setMinimumHeight(24 if parent.tiny else 36)
            actions.addWidget(b)
        outer.addLayout(actions)
        self.category.currentIndexChanged.connect(self.load_category)
        self.items.currentRowChanged.connect(self.update_arrows)
        self.items.order_changed.connect(self.record_order)
        self.set_drag_mode(False)
        self.load_category()

    def set_drag_mode(self, enabled):
        viewport = self.items.viewport()
        QScroller.scroller(viewport).stop()
        QScroller.ungrabGesture(viewport)
        self.items.setDragDropMode(QAbstractItemView.InternalMove if enabled else QAbstractItemView.NoDragDrop)
        self.items.setDefaultDropAction(Qt.MoveAction)
        self.items.setDragEnabled(enabled)
        self.items.setAcceptDrops(enabled)
        self.items.setDropIndicatorShown(enabled)
        if not enabled:
            viewport.setAttribute(Qt.WA_AcceptTouchEvents, True)
            QScroller.grabGesture(viewport, QScroller.LeftMouseButtonGesture)

    def load_category(self, *_):
        self.key = self.category.currentData()
        entries = self.catalog[self.key][1]
        names = dict(entries)
        order = ordered_items(names, self.changes.get(self.key, read_layout_order(self.key)))
        self.loading = True
        self.items.clear()
        for ident in order:
            item = QListWidgetItem(names[ident])
            item.setData(Qt.UserRole, ident)
            item.setToolTip(names[ident])
            self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)
        self.loading = False
        self.update_arrows()

    def record_order(self, *_):
        if not self.loading and self.key:
            self.changes[self.key] = [self.items.item(i).data(Qt.UserRole)
                                      for i in range(self.items.count())]
            self.update_arrows()

    def update_arrows(self, *_):
        index = self.items.currentRow()
        self.up.setEnabled(index > 0)
        self.down.setEnabled(0 <= index < self.items.count() - 1)

    def move_item(self, delta):
        index = self.items.currentRow()
        target = index + delta
        if index < 0 or not 0 <= target < self.items.count():
            return
        self.items.insertItem(target, self.items.takeItem(index))
        self.items.setCurrentRow(target)
        self.items.scrollToItem(self.items.item(target))
        self.record_order()

    def reset_order(self):
        self.changes[self.key] = []
        self.load_category()


class TemperatureHistory(QWidget):
    """Small bounded in-memory plot, sampled at 5 seconds, no external libraries."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = deque(maxlen=180)
        self.setMinimumHeight(105)
        self.last_sample = 0
        self.colors = ('#ff8e85', '#75c3ff', '#f4c36c')

    def sample(self, status):
        now = time.monotonic()
        if now - self.last_sample < 5:
            return
        self.last_sample = now
        active = status.get('toolhead', {}).get('extruder', '')
        chamber = next((n for n in status if n.startswith(('heater_generic ', 'temperature_sensor ')) and 'chamber' in n.lower()), '')
        values = [fval(status.get(n, {}).get('temperature'), float('nan')) for n in (active, 'heater_bed', chamber)]
        self.samples.append((now, active, values))
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#131e2d'))
        p.setPen(QColor('#aab8ce'))
        x = 6
        for title, color in zip(('Nozzle', 'Bed', 'Chamber'), self.colors):
            p.setPen(QColor(color))
            p.drawText(x, 15, title)
            x += p.fontMetrics().horizontalAdvance(title) + 13
        samples = list(self.samples)
        values = [v for _, _, row in samples for v in row if math.isfinite(v)]
        if len(samples) < 2 or not values:
            p.drawText(self.rect(), Qt.AlignCenter, 'Collecting temperature data…')
            return
        x0, y0, w, h = 32, 23, max(1, self.width()-38), max(1, self.height()-43)
        lo, hi = min(0, min(values)), max(50, max(values)+5)
        p.setPen(QColor('#7a8da4'))
        p.drawText(2, y0+10, str(round(hi)))
        p.drawText(2, y0+h, str(round(lo)))
        window = min(900, max(60, samples[-1][0]-samples[0][0]))
        t0 = samples[-1][0] - window
        for i, color in enumerate(self.colors):
            p.setPen(QPen(QColor(color), 2))
            prev = None
            prev_tool = None
            prev_stamp = 0
            for stamp, tool, row in samples:
                v = row[i]
                if not math.isfinite(v) or stamp < t0:
                    prev = None
                    continue
                x = x0 + (stamp-t0)/window*w
                y = y0 + (hi-v)/(hi-lo)*h
                if prev is not None and stamp - prev_stamp <= 15 and (i != 0 or tool == prev_tool):
                    p.drawLine(round(prev[0]), round(prev[1]), round(x), round(y))
                prev, prev_tool, prev_stamp = (x,y), tool, stamp
        p.setPen(QColor('#aab8ce'))
        p.drawText(x0, self.height()-3, f'-{round(window/60)} min')
        p.drawText(max(x0, self.width()-35), self.height()-3, 'Now')


class ScreenShade(QWidget):
    """Software dim/blank overlay; first touch wakes without activating a control."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.opacity = 150
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.hide()

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, self.opacity))

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()
        self.owner.wake_screen()


class UnlockSlider(QWidget):
    unlocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(58)
        self.position = 0.0
        self.dragging = False
        self.start = 0
        self.handle = 48

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#26364e'))
        painter.drawRoundedRect(QRectF(2, 4, max(1, self.width()-4), self.height()-8), 20, 20)
        painter.setPen(QColor('#b9cce4'))
        painter.drawText(self.rect(), Qt.AlignCenter, tr('Slide to unlock →'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#d4e3f7'))
        x = 4 + self.position * max(1, self.width()-self.handle-8)
        painter.drawRoundedRect(QRectF(x, 7, self.handle, self.height()-14), 17, 17)
        painter.setPen(QColor('#26364e'))
        painter.drawText(QRectF(x, 7, self.handle, self.height()-14), Qt.AlignCenter, '›')

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and 0 <= event.x() <= self.handle + 9:
            self.dragging = True
            self.start = event.x()
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.position = max(0.0, min(1.0, (event.x()-self.start) / max(1, self.width()-self.handle-8)))
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        unlock = self.dragging and self.position >= .90
        self.dragging = False
        self.position = 0.0
        self.update()
        event.accept()
        if unlock:
            self.unlocked.emit()


class CleaningLock(QDialog):
    """Touch cleaning lock: only a continuous slider drag unlocks (not a tap)."""
    def __init__(self, owner):
        super().__init__(owner, Qt.FramelessWindowHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setStyleSheet(owner.styleSheet())
        l = QVBoxLayout(self)
        l.addStretch()
        title = label('Screen locked for cleaning', 'title')
        title.setAlignment(Qt.AlignCenter)
        l.addWidget(title)
        subtitle = label('Slide to unlock →', 'metric')
        subtitle.setAlignment(Qt.AlignCenter)
        l.addWidget(subtitle)
        self.slider = UnlockSlider(self)
        self.slider.unlocked.connect(self.accept)
        l.addWidget(self.slider)
        l.addWidget(label('Drag the handle all the way to the right.', 'caption'))
        l.addStretch()

    def reject(self):
        # Escape, accidental taps and window manager close do not unlock.
        pass

    def closeEvent(self, event):
        event.ignore()


class Screen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('VeloxScreen')
        self.setObjectName('veloxRoot')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.theme = dict(THEME)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        screen_width = available.width() if available is not None else 800
        screen_height = available.height() if available is not None else 480
        self.compact, self.tiny, self.ui_scale = display_profile(
            screen_width, screen_height, conf('display', 'ui_scale', 'auto'))
        self.setStyleSheet(display_style(STYLE, self.ui_scale))
        self.resize(screen_width, screen_height)
        self.s = {}
        self.all_objects = []
        self.network = Network(conf('connection', 'moonraker_url', 'http://127.0.0.1:7125'))
        self.network.data.connect(self.refresh)
        self.network.err.connect(self.error)
        self.network.reply.connect(self.response)
        self.network.gcode_messages.connect(self.handle_gcode_messages)
        self.network.emergency_triggered.connect(self.emergency_confirmed)
        self.network.printer_health.connect(self.update_printer_health)
        self.network.recovery_result.connect(self.show_recovery_result)
        self.network.shutdown_result.connect(self.show_shutdown_result)
        self.network.print_metadata.connect(self.receive_print_metadata)
        self.network.print_action_result.connect(self.print_action_result)
        self.network.live_z_result.connect(self.live_z_result)
        self.network.z_save_result.connect(self.z_save_result)
        self.network.motion_result.connect(self.toolhead_motion_result)
        self.network.files_result.connect(self.receive_files)
        self.network.file_preview.connect(self.receive_file_preview)
        self.network.file_start_result.connect(self.file_start_result)
        self.network.exclude_result.connect(self.object_exclusion_result)
        self._file_entries = []
        self._file_cards = {}
        self._file_generation = 0
        self._file_page = 0
        self._file_start_pending = False
        self._file_start_finished_at = 0.0
        self._exclude_pending = False
        self._exclude_finished_at = 0.0
        self._exclude_dialog = None
        self._toolhead_pending = ''
        self._toolhead_finished_at = 0.0
        self._toolhead_uncertain = False
        self._toolhead_feedback = ''
        self._homing_frame = 0
        self.dashboard_home_buttons = {}
        self._homing_timer = QTimer(self)
        self._homing_timer.setInterval(160)
        self._homing_timer.timeout.connect(self.animate_toolhead_busy)
        self._z_pending = False
        self._z_save_pending = False
        self._z_save_locked = False
        self._z_save_accepted = False
        self._z_after_print = None
        self._z_after_ready = False
        self._z_step = .01
        self._print_meta_name = ''
        self._print_meta = {}
        self._metadata_requested = 0.0
        self._print_action_pending = False
        self._live_status = {}
        self._live_status_at = 0.0
        self._sensor_previous = {}
        self._heating_previous = {}
        self._history = deque(maxlen=150)
        self._history_path = PRINTER_CONFIG_DIR / 'veloxscreen_notifications.json'
        self._history_dirty = False
        self._event_seen = {}
        self._load_history()
        self._last_input = time.monotonic()
        self._clean_lock = None
        self._shade = None
        self._wake_suppress = 0.0
        self._brightness_path = self.find_backlight()
        self._dpms_available = bool(os.environ.get("DISPLAY") and shutil.which("xset"))
        self._dpms_off = False
        self._dpms_enabled_by_us = False
        if self._brightness_path is not None and conf('display', 'brightness', ''):
            self.set_brightness(bounded_number(conf('display', 'brightness', '100'), 100, 5, 100))
        # Default to confirmation even for old configs with confirm_commands=false.
        self.confirm_shutdown = conf('behavior', 'confirm_shutdown', 'true').strip().lower() not in ('false', 'no', '0', 'off')
        self._shutdown_pending = False
        self._shutdown_idle = False
        self._health_state = 'unknown'
        self._health_message = ''
        self._local_estop = False
        self._temperature_status = None
        self.preset_status_buttons = []
        self.busy_files = False
        self.selected_file = ''
        self.pages = {}
        self.nav_buttons = {}
        self.layout_groups = {}
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
        # Display-only jog settings; no changes to Klipper motor direction or G28.
        self.jog_invert = {axis: conf('jog', 'invert_' + axis.lower(), 'false').strip().lower()
                           in ('true', 'yes', '1', 'on') for axis in ('X', 'Y', 'Z')}
        self.jog_speed = {
            'X': max(1.0, min(500.0, fval(conf('jog', 'speed_x', '100'), 100))),
            'Y': max(1.0, min(500.0, fval(conf('jog', 'speed_y', '100'), 100))),
            'Z': max(0.1, min(50.0, fval(conf('jog', 'speed_z', '10'), 10))),
        }
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
        # Auto-open only on a transition to printing, not on every refresh.
        self._previous_print_state = None
        self.print_fan_widgets = {}
        self._print_fan_signature = None
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self.hide_notification)
        self._build()
        self.update_toolhead_control({})
        self._build_notification_banner()
        self._shade = ScreenShade(self)
        QApplication.instance().installEventFilter(self)
        self.comfort_timer = QTimer(self)
        self.comfort_timer.timeout.connect(self.check_idle_display)
        self.comfort_timer.start(1000)
        self.history_timer = QTimer(self)
        self.history_timer.timeout.connect(self.save_history)
        self.history_timer.start(5000)
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
        sidebar.setFixedWidth(142 if self.compact else 184 if not self.tiny else 142)
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
        # Match the main content's one-finger kinetic scrolling.
        # FingerScrollArea accepts emulated mouse drags from DSI touch drivers,
        # while preserving short taps on the navigation buttons.
        nav_scroll = FingerScrollArea()
        nav_panel = QWidget()
        nav_panel.setObjectName('veloxNavPanel')
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 1, 0)
        nav_layout.setSpacing(3)
        nav_scroll.setWidget(nav_panel)
        sl.addWidget(nav_scroll, 1)
        self.stack = QStackedWidget()
        sections = [('Dashboard', self.make_dashboard), ('Print', self.make_print_view), ('Recovery', self.make_recovery_page), ('Tools', self.make_tools),
                    ('Temperaturen', self.make_temperatures), ('Steuerung', self.make_control),
                    ('LED Effects', self.make_led_effects), ('Dateien', self.make_files), ('Kalibrierung', self.make_calib),
                    ('Konsole', self.make_console), ('Makros', self.make_macros),
                    ('Network', self.make_network), ('Notifications', self.make_notifications), ('Einstellungen', self.make_settings)]
        self.nav_layout = nav_layout
        self.nav_defaults = [name for name, _ in sections]
        self.nav_order = list(self.nav_defaults)
        self.compact_nav = None
        if self.compact:
            self.compact_nav = QComboBox()
            self.compact_nav.setMinimumHeight(30 if self.tiny else 38)
            self.compact_nav.addItems([tr(name) for name, _ in sections])
            self.compact_nav.currentIndexChanged.connect(
                lambda index: self.navigate(self.nav_order[index]) if 0 <= index < len(self.nav_order) else None)
            # Insert into the content column after its layout exists.
        for name, builder in sections:
            nav = QPushButton(tr(name))
            nav.setObjectName('nav')
            nav.setCheckable(True)
            nav.clicked.connect(partial(self.navigate, name))
            nav_layout.addWidget(nav)
            self.nav_buttons[name] = nav
            self.pages[name] = self._page(name, builder)
            self.stack.addWidget(self.pages[name])
        nav_layout.addStretch()
        sl.addWidget(label('v0.4.7 alpha', 'caption'))
        # IMPORTANT: Qt otherwise deletes self.connection and all nav buttons.
        outer.addWidget(sidebar)
        sidebar.setVisible(not self.compact)
        content_side = QWidget()
        content_layout = QVBoxLayout(content_side)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        if self.compact_nav is not None:
            content_layout.addWidget(self.compact_nav)
        # No service controls in the header: settings owns the local restart action.
        # This also gives the 800x480 screens more usable height.
        self.health_banner = QPushButton('Printer status')
        self.health_banner.setMinimumHeight(32)
        self.health_banner.clicked.connect(lambda: self.navigate('Recovery'))
        self.health_banner.hide()
        content_layout.addWidget(self.health_banner)
        self.quick_bar = FingerScrollArea()
        self.quick_bar.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.quick_bar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.quick_bar.setFixedHeight(round((38 if self.tiny else 52) * max(1.0, bounded_number(conf('display', 'button_scale', '1'), 1, .8, 1.5))))
        self.quick_panel = QWidget()
        self.quick_panel.setObjectName('veloxQuickPanel')
        self.quick_layout = QHBoxLayout(self.quick_panel)
        self.quick_layout.setContentsMargins(4, 2, 4, 2)
        self.quick_layout.setSpacing(4)
        self.quick_bar.setWidget(self.quick_panel)
        self.quick_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        content_layout.addWidget(self.quick_bar)
        # Shared fixed header: emergency stop is always at the top right on
        # Dashboard and Print, outside all scrolling/reorderable controls.
        self.safety_header = QWidget()
        safety_header_layout = QHBoxLayout(self.safety_header)
        safety_header_layout.setContentsMargins(9, 5, 9, 3)
        safety_header_layout.setSpacing(6)
        self.safety_header_title = label('Dashboard', 'title')
        safety_header_layout.addWidget(self.safety_header_title, 1)
        self.safety_stop_button = self.make_emergency_button()
        safety_header_layout.addWidget(self.safety_stop_button, 0, Qt.AlignRight | Qt.AlignVCenter)
        content_layout.addWidget(self.safety_header)
        content_layout.addWidget(self.stack, 1)
        outer.addWidget(content_side, 1)
        self.apply_layout_orders()
        self.rebuild_quick_bar()
        initial = conf('display', 'start_page', 'Dashboard')
        self.navigate(initial if initial in self.pages else 'Dashboard')

    def layout_block(self, page, parent_layout, key, title):
        if page not in self.layout_groups:
            holder = QWidget()
            inner = QVBoxLayout(holder)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(7)
            parent_layout.addWidget(holder)
            self.layout_groups[page] = (inner, {})
        inner, blocks = self.layout_groups[page]
        frame = QWidget()
        content = QVBoxLayout(frame)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(7)
        inner.addWidget(frame)
        blocks[key] = (frame, title)
        return content

    def apply_layout_orders(self):
        for key, (layout, blocks) in self.layout_groups.items():
            for ident in ordered_items(blocks, read_layout_order(key)):
                widget = blocks[ident][0]
                layout.removeWidget(widget)
                layout.addWidget(widget)
        active = next((name for name, page in self.pages.items()
                       if page is self.stack.currentWidget()), 'Dashboard')
        self.nav_order = ordered_items(self.nav_defaults, read_layout_order('nav_order'))
        for i, name in enumerate(self.nav_order):
            widget = self.nav_buttons[name]
            self.nav_layout.removeWidget(widget)
            self.nav_layout.insertWidget(i, widget)
        if self.compact_nav is not None:
            self.compact_nav.blockSignals(True)
            self.compact_nav.clear()
            self.compact_nav.addItems([tr(name) for name in self.nav_order])
            self.compact_nav.setCurrentIndex(self.nav_order.index(active))
            self.compact_nav.blockSignals(False)

    def layout_catalog(self):
        """Return stable IDs and translated labels, plus the default ordering."""
        groups = {'nav_order': ('Navigation', [(name, tr(name)) for name in self.nav_defaults])}
        titles = {'dashboard_order': 'Dashboard blocks', 'print_order': 'Print blocks',
                  'control_order': 'Control blocks', 'tools_order': 'Tools blocks',
                  'temperatures_order': 'Temperature blocks', 'macros_order': 'Macro blocks',
                  'settings_order': 'Settings blocks'}
        for key, (_, blocks) in self.layout_groups.items():
            groups[key] = (titles[key], [(ident, tr(title)) for ident, (_, title) in blocks.items()])
        fans = sorted(n for n in self.all_objects if n == 'fan' or
                      n.startswith(('fan_generic ', 'heater_fan ', 'temperature_fan ')))
        temps = sorted((n for n in self.all_objects if n == 'extruder' or
                       re.fullmatch(r'extruder\d+', n) or n == 'heater_bed' or
                       n.startswith('heater_generic ')),
                       key=lambda n: (0, 0) if n == 'extruder' else
                       (0, int(n[8:])) if re.fullmatch(r'extruder\d+', n) else
                       (1, 0) if n == 'heater_bed' else (2, n))
        temps += sorted(n for n in self.all_objects if n.startswith(('temperature_sensor ', 'temperature_fan ')))
        presets = list(config['temperature_presets']) if config.has_section('temperature_presets') else []
        for key, title, defaults in (
                ('fan_order', 'Fans', fans), ('temperature_order', 'Temperature tiles', temps),
                ('macro_order', 'Macro buttons', sorted(self.macro_commands, key=str.casefold)),
                ('preset_order', 'Temperature presets', presets)):
            # Keep saved but currently unavailable IDs; changing a layout while offline
            # must not discard a disconnected tool or a temporarily absent macro.
            groups[key] = (title, [(n, n) for n in dict.fromkeys(defaults + read_layout_order(key))])
        return groups

    def open_layout_editor(self):
        editor = LayoutEditor(self, self.layout_catalog())
        if editor.exec_() != QDialog.Accepted or not editor.changes:
            return
        had_section = config.has_section('layout')
        old = dict(config.items('layout')) if had_section else {}
        if not had_section:
            config.add_section('layout')
        try:
            for key, order in editor.changes.items():
                # Keep the old fan_order format compatible with existing configs.
                value = ', '.join(order) if key == 'fan_order' else json.dumps(order, ensure_ascii=False)
                config.set('layout', key, value)
            save_config()
        except (OSError, ValueError) as exc:
            config.remove_section('layout')
            if had_section:
                config.add_section('layout')
                for key, value in old.items():
                    config.set('layout', key, value)
            warn(self, 'Customize layout', 'Could not save layout: ' + str(exc))
            return
        self.apply_layout_orders()
        if 'macro_order' in editor.changes:
            self._render_macro_buttons()
        if 'preset_order' in editor.changes:
            self.rebuild_presets()
        # Dynamic temperature and fan views compare ordered signatures on the next poll.

    def _page(self, name, builder):
        scroll = FingerScrollArea()
        container = QWidget()
        container.setObjectName('veloxPage')
        content = QVBoxLayout(container)
        content.setContentsMargins(9, 7, 9, 9)
        content.setSpacing(7)
        if name not in ('Dashboard', 'Print'):
            content.addWidget(label(name, 'title'))
        builder(content)
        content.addStretch()
        scroll.setWidget(container)
        return scroll

    def navigate(self, name):
        self.stack.setCurrentWidget(self.pages[name])
        self.safety_header_title.setText(tr(name))
        self.safety_header.setVisible(name in ('Dashboard', 'Print'))
        if self.compact_nav is not None:
            names = self.nav_order
            self.compact_nav.blockSignals(True)
            self.compact_nav.setCurrentIndex(names.index(name))
            self.compact_nav.blockSignals(False)
        for n, b in self.nav_buttons.items():
            b.setChecked(n == name)
        if name == 'Dateien':
            self.reload_print_files()
        elif self._file_cards:
            self._file_generation += 1
            self.network.request_file_previews(self._file_generation, [])
        if name == 'Network':
            self.scan_wifi()
        if name == 'Notifications':
            self.render_history()


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
        if getattr(self, '_shade', None) is not None:
            self._shade.setGeometry(self.rect())

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
        self.preset_status_buttons = []
        entries = dict(config.items('temperature_presets')) if config.has_section('temperature_presets') else {}
        entries = [(name, entries[name]) for name in ordered_items(entries, read_layout_order('preset_order'))]
        for grid, columns in ((getattr(self, 'dashboard_presets', None), 3),
                              (getattr(self, 'temp_preset_grid', None), 2)):
            if grid is None:
                continue
            self._clear_layout(grid)
            for i, (name, content) in enumerate(entries):
                b = button(name, partial(self.apply_temp_preset, name, content))
                b.setMinimumHeight(39)
                self.preset_status_buttons.append((b, name, content))
                grid.addWidget(b, i // (1 if self.tiny else columns), i % (1 if self.tiny else columns))
            if not entries:
                grid.addWidget(label('No temperature presets. Tap Edit presets.', 'caption'), 0, 0)
        self.update_preset_highlights(self._temperature_status)
        if hasattr(self, 'quick_layout'):
            self.rebuild_quick_bar()

    @staticmethod
    def paint_heat_state(widget, state):
        # Fixed high-contrast colors are independent of user-selected theme colors.
        colors = {'heating': ('#8c242c', '#ff7373'),
                  'ready': ('#205d42', '#6bd6a0'),
                  'cooling': ('#79501b', '#efbc64')}
        if state in colors:
            bg, border = colors[state]
            style = ('background-color: %s; color: white; border: 1px solid %s; '
                     'border-radius: 5px; padding: 3px; font-weight: bold;' % (bg, border))
        else:
            style = ''
        if widget.styleSheet() != style:
            widget.setStyleSheet(style)

    def show_heater_target(self, widget, entry, editable=False):
        state = heater_state(entry)
        if state == 'unknown':
            text = tr('Target unavailable')
        elif state == 'off':
            text = tr('Heater off') + ' · 0 °C'
        else:
            prefix = {'heating': 'Heating to', 'ready': 'At target', 'cooling': 'Cooling to'}[state]
            text = tr(prefix) + ' ' + fmt(entry.get('target'), 0) + ' °C'
        if editable and state != 'unknown':
            if state in ('heating', 'ready', 'cooling'):
                text = tr(prefix) + '\n' + fmt(entry.get('target'), 0) + ' °C  ✎'
            else:
                text = tr('Heater off') + '\n0 °C  ✎'
        widget.setText(text)
        self.paint_heat_state(widget, state)

    def update_preset_highlights(self, status):
        for widget, name, content in self.preset_status_buttons:
            state = 'off'
            if status is not None:
                targets = self.resolve_preset_targets(content, status)
                state = matching_preset_state(targets, status)
            suffix = {'heating': 'Heating', 'ready': 'At target', 'cooling': 'Cooling'}.get(state)
            widget.setText(tr(name) + ('\n' + tr(suffix) if suffix else ''))
            self.paint_heat_state(widget, state)

    def clear_heat_status(self):
        # Do not present stale targets as live after an error or disconnect.
        self._temperature_status = None
        self.update_preset_highlights(None)
        for widgets in (self.dash_temp_widgets, self.temp_widgets, self.print_temp_widgets):
            for name, (reading, target) in widgets.items():
                reading.setText('-- °C')
                if target is not None and (name == 'extruder' or re.fullmatch(r'extruder\d+', name)
                        or name == 'heater_bed' or name.startswith('heater_generic ')):
                    self.show_heater_target(target, {})
                    if isinstance(target, QPushButton):
                        target.setEnabled(False)

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

    def make_print_view(self, l):
        """Live printing control page (opens on print start, stays navigable)."""
        f, c = card()
        if self.tiny:
            c.setContentsMargins(4, 3, 4, 3)
            c.setSpacing(3)
        self.print_status_label = label('Waiting for printer status...', 'caption')
        self.print_status_label.setWordWrap(True)
        c.addWidget(self.print_status_label)
        self.print_status_label.setTextFormat(Qt.PlainText)
        self.print_progress_label = label('-- %', 'metricSmall' if self.tiny else 'metric')
        self.print_progress_label.setAlignment(Qt.AlignCenter)
        c.addWidget(self.print_progress_label)
        self.print_progress_bar = QProgressBar()
        self.print_progress_bar.setRange(0, 1000)
        self.print_progress_bar.setTextVisible(False)
        self.print_progress_bar.setFixedHeight(9)
        c.addWidget(self.print_progress_bar)
        times = QHBoxLayout()
        self.remaining_label = label('—', 'etaValueSmall' if self.tiny else 'etaValue')
        self.eta_label = label('—', 'etaValueSmall' if self.tiny else 'etaValue')
        for title, widget in (('TIME REMAINING', self.remaining_label), ('FINISH AT · EST.', self.eta_label)):
            panel, box = card()
            if self.tiny:
                box.setContentsMargins(4, 4, 4, 4)
                box.setSpacing(2)
            caption = label(title, 'caption')
            caption.setAlignment(Qt.AlignCenter)
            box.addWidget(caption)
            widget.setAlignment(Qt.AlignCenter)
            box.addWidget(widget)
            times.addWidget(panel)
        c.addLayout(times)
        self.print_estimate_info = label('Waiting for print data', 'caption')
        c.addWidget(self.print_estimate_info)
        l.addWidget(f)

        actions = QHBoxLayout()
        self.print_actions = {}
        for name in ('pause', 'resume', 'cancel'):
            b = button(name.capitalize(), partial(self.request_print_action, name))
            b.setEnabled(False)
            self.print_actions[name] = b
            actions.addWidget(b)
        l.addLayout(actions)
        self.print_action_status = label('', 'caption')
        l.addWidget(self.print_action_status)
        l.addWidget(button('Restart / Recovery options', self.open_recovery_menu))
        self.exclude_button = button('Exclude Object', self.open_exclude_objects)
        self.exclude_button.setEnabled(False)
        self.exclude_hint = label('Object exclusion requires [exclude_object] and object-marked G-code.', 'caption')
        l.addWidget(self.exclude_button)
        l.addWidget(self.exclude_hint)

        root = l
        l = self.layout_block('print_order', root, 'job_details', 'Print details / Preview')
        f, c = card('Print details')
        self.print_thumb = QLabel(tr('No preview'))
        self.print_thumb.setAlignment(Qt.AlignCenter)
        self.print_thumb.setMinimumHeight(65)
        self.print_thumb.setMaximumHeight(120 if self.tiny else 160)
        self.print_thumb.setTextFormat(Qt.PlainText)
        c.addWidget(self.print_thumb)
        self.print_details = label('Layer — · Filament — · Active tool —', 'readout')
        self.print_details.setTextFormat(Qt.PlainText)
        c.addWidget(self.print_details)
        l.addWidget(f)
        l = self.layout_block('print_order', root, 'motion', 'Live motion')
        grid = QGridLayout()
        self.print_metrics = {}
        for index, (title, unit, key) in enumerate((
                ('Actual speed', 'mm/s', 'speed'),
                ('Extrusion flow', 'mm³/s', 'flow'),
                ('Accel limit', 'mm/s²', 'accel'))):
            f, c = card(title)
            value = label('--', 'metric')
            self.print_metrics[key] = value
            c.addWidget(value)
            c.addWidget(label(unit, 'caption'))
            cols = 1 if self.tiny else 2 if self.compact else 3
            grid.addWidget(f, index // cols, index % cols)
        l.addLayout(grid)

        l = self.layout_block('print_order', root, 'temperatures', 'Print temperatures')
        f, c = card('Print temperatures')
        self.print_temp_info = label('Waiting for heater data...', 'caption')
        c.addWidget(self.print_temp_info)
        self.print_temp_grid = QGridLayout()
        self.print_temp_grid.setSpacing(5)
        c.addLayout(self.print_temp_grid)
        self.print_temp_widgets = {}
        self._print_temp_signature = None
        self._print_temp_live = False
        l.addWidget(f)

        l = self.layout_block('print_order', root, 'overrides', 'Speed / Flow Overrides')
        l.addWidget(label('Live overrides  ·  M220 / M221', 'caption'))
        self.add_override_controls(l)

        l = self.layout_block('print_order', root, 'fans', 'Fans')
        f, c = card('Fans  ·  live control')
        self.print_fan_area = QVBoxLayout()
        self.print_fan_area.setSpacing(7)
        c.addLayout(self.print_fan_area)
        l.addWidget(f)
        l.addWidget(label('Manual fans only. Automatic fans are hidden on this page.', 'caption'))
        l = self.layout_block('print_order', root, 'babystep', 'Live Z offset')
        f, c = card('Live Z offset · Babystepping')
        self.print_z_offset = label('Z offset: —', 'metric')
        c.addWidget(self.print_z_offset)
        self.z_buttons = []
        self.z_step_combo = QComboBox()
        for step in Z_STEPS:
            self.z_step_combo.addItem(f'{step:.3f} mm', step)
        self.z_step_combo.setCurrentIndex(1)
        self.z_step_combo.currentIndexChanged.connect(lambda _: setattr(self, '_z_step', float(self.z_step_combo.currentData())))
        c.addWidget(self.z_step_combo)
        bar = QHBoxLayout()
        for sign, title in ((-1, 'Z −'), (1, 'Z +')):
            b = button(title, lambda checked=False, sign=sign: self.adjust_print_z(sign * self._z_step))
            self.z_buttons.append(b)
            b.setEnabled(False)
            bar.addWidget(b)
        c.addLayout(bar)
        self.z_reset_button = button('Reset live Z to 0', self.reset_live_z)
        self.z_save_button = button('Save Z offset', self.request_z_save)
        self.z_cancel_button = button('Cancel save reminder', self.cancel_z_reminder)
        for b in (self.z_reset_button, self.z_save_button, self.z_cancel_button):
            b.setEnabled(False)
            c.addWidget(b)
        self.z_cancel_button.hide()
        self.z_save_hint = label('', 'caption')
        self.z_save_hint.setWordWrap(True)
        c.addWidget(self.z_save_hint)
        self.z_action_status = label('', 'caption')
        self.z_action_status.setWordWrap(True)
        c.addWidget(self.z_action_status)
        c.addWidget(label('Live offset in mm. Verify clearance and direction. Saving permanently restarts Klipper.', 'caption'))
        l.addWidget(f)
        l = self.layout_block('print_order', root, 'temperature_history', 'Temperature history')
        f, c = card('Temperature history')
        self.temp_history = TemperatureHistory()
        c.addWidget(self.temp_history)
        l.addWidget(f)

    def make_emergency_button(self):
        stop = QPushButton(tr('EMERGENCY STOP'))
        stop.setObjectName('danger')
        stop.setMinimumHeight(36 if self.tiny else 44)
        stop.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        stop.setStyleSheet('QPushButton#danger { font-size: 12px; padding: 4px 7px; min-height: 28px; }')
        stop.setToolTip(tr('Immediately stops Klipper. Requires firmware restart/recovery.'))
        stop.clicked.connect(self.request_emergency_stop)
        return stop

    def request_emergency_stop(self):
        """Always confirm, even when normal macro confirmations are disabled."""
        message = ('EMERGENCY STOP will immediately shut down Klipper.\n\n'
                   'Motors and heaters will be disabled and the current print will fail.\n'
                   'A firmware restart and printer recovery may be required.\n\n'
                   'Really trigger emergency stop?')
        if QMessageBox.warning(self, 'EMERGENCY STOP', message,
                               QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) != QMessageBox.Yes:
            return
        self.network.emergency_stop()

    def make_recovery_page(self, l):
        self.health_title = label('Waiting for printer status', 'title')
        l.addWidget(self.health_title)
        # Plain text preserves Klipper's exact message without interpreting markup.
        self.health_detail = label('Waiting for Moonraker...')
        self.health_detail.setTextFormat(Qt.PlainText)
        self.health_detail.setWordWrap(True)
        l.addWidget(self.health_detail)
        self.health_result = label('', 'caption')
        self.health_result.setTextFormat(Qt.PlainText)
        l.addWidget(self.health_result)
        l.addWidget(label('Correct the underlying fault before restarting. '
                          'A failed print is not resumed automatically.', 'caption'))
        for title, action in (('Firmware Restart', 'firmware'),
                              ('Restart Klipper', 'klipper'),
                              ('Reboot Host (Raspberry Pi)', 'host')):
            l.addWidget(button(title, partial(self._choose_recovery_action, action), action == 'firmware'))
        l.addWidget(button('Dashboard', lambda: self.navigate('Dashboard')))

    def emergency_confirmed(self):
        self._local_estop = True
        self.update_printer_health({'state': 'shutdown', 'message': 'Emergency stop accepted by Moonraker. Waiting for Klipper status.'})
        self.navigate('Recovery')

    def show_recovery_result(self, message):
        self.health_result.setText(message)

    def update_printer_health(self, info):
        state = info.get('state', 'unknown')
        message = info.get('message', '')
        previous = self._health_state
        self._health_state = state
        self._health_message = message
        if state != 'ready':
            if state in ('shutdown', 'startup', 'error') and self._toolhead_uncertain:
                self._toolhead_uncertain = False
                self._toolhead_pending = ''
                self._toolhead_finished_at = 0.0
            self._live_status_at = 0.0
            if self._z_after_print is not None:
                self.cancel_z_reminder()
            self._heating_previous.clear()
            self._sensor_previous.clear()
            self.update_job_controls({})
            self.update_toolhead_control({})
            self.remaining_label.setText('—')
            self.eta_label.setText('—')
            self.print_estimate_info.setText('Printer status unavailable')
            self.print_progress_label.setText('— %')
            self.wake_screen()
            self.release_clean_lock()
        if state != 'ready':
            self._shutdown_idle = False
            self.update_shutdown_button()
        if state == 'ready':
            self._local_estop = False
            self.health_title.setText('READY')
            self.health_title.setStyleSheet('color: #66d49b; font-weight: bold;')
            self.health_detail.setText(message or 'Klipper is ready.')
            self.health_banner.hide()
            if previous != 'ready':
                self.health_result.setText('Printer ready.')
                if previous != 'unknown':
                    self.record_event('Printer', 'Printer ready', 'Klipper is ready.', popup=True)
            return
        fault = state in ('error', 'shutdown', 'offline', 'disconnected')
        explicit_stop = ('m112' in message.lower() or 'emergency stop' in message.lower())
        if state == 'shutdown' and (self._local_estop or explicit_stop):
            title = 'EMERGENCY STOP / SHUTDOWN'
        else:
            title = {'shutdown': 'KLIPPER SHUTDOWN', 'error': 'PRINTER ERROR',
                     'offline': 'MOONRAKER OFFLINE', 'disconnected': 'KLIPPER DISCONNECTED',
                     'startup': 'KLIPPER STARTING'}.get(state, 'PRINTER STATUS: ' + str(state).upper())
        color = '#ff7474' if fault else '#f4c36c'
        self.health_title.setText(title)
        self.health_title.setStyleSheet('color: ' + color + '; font-weight: bold;')
        self.health_detail.setText(message or 'No detailed Klipper message available yet.')
        self.health_banner.setText(title + '  ›')
        self.health_banner.setStyleSheet('QPushButton { background: ' + ('#922b35' if fault else '#77551d') + '; color: white; font-weight: bold; }')
        self.health_banner.show()
        self._print_temp_live = False
        self.clear_heat_status()
        # Navigate once per fault transition, not on every poll. A persistent banner
        # remains visible on other pages.
        if fault and state != previous:
            self.record_event('Warning', title, message, popup=False)
            self.health_result.setText('')
            self.navigate('Recovery')

    def open_recovery_menu(self):
        self.navigate('Recovery')

    def _choose_recovery_action(self, action):
        descriptions = {
            'klipper': ('Restart Klipper',
                        'Restart the Klipper software process? The current print will not resume.'),
            'firmware': ('Firmware Restart',
                         'Restart Klipper and reinitialize the connected MCUs? '
                         'Do this only when the hardware fault is resolved.'),
            'host': ('Reboot Host',
                     'Reboot the Raspberry Pi / host operating system? '
                     'This disconnects the touchscreen, SSH and Moonraker. '
                     'Do not reboot during an active print.'),
        }
        if action not in descriptions:
            return
        name, explanation = descriptions[action]
        if QMessageBox.warning(self, name, explanation + '\n\nContinue?',
                               QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) != QMessageBox.Yes:
            return
        dialog = getattr(self, '_recovery_dialog', None)
        if dialog is not None:
            dialog.accept()
        self.health_result.setText('Sending ' + name + '...')
        self.network.recovery_action(action)

    def update_print_fans(self, st):
        """Show only manually controllable Klipper fans on the print page."""
        fans = [x for x in self.all_objects if x == 'fan' or x.startswith('fan_generic ')]
        fans = ordered_items(sorted(fans), read_layout_order('fan_order'))
        signature = tuple(fans)
        if signature != self._print_fan_signature:
            self._print_fan_signature = signature
            self.print_fan_widgets = {}
            while self.print_fan_area.count():
                item = self.print_fan_area.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            for name in fans:
                frame, content = card(name)
                info = label('-- %', 'readout')
                content.addWidget(info)
                slider = None
                if name == 'fan' or name.startswith('fan_generic '):
                    slider = QSlider(Qt.Horizontal)
                    slider.setRange(0, 100)
                    slider.setSingleStep(1)
                    slider.setPageStep(5)
                    slider.setMinimumHeight(39)
                    slider.setStyleSheet(fan_slider_style(self.theme))
                    slider.valueChanged.connect(partial(self.print_fan_slider_changed, name))
                    slider.sliderReleased.connect(partial(self.print_fan_slider_released, name))
                    content.addWidget(slider)
                else:
                    content.addWidget(label('Automatic fan  ·  read-only', 'caption'))
                self.print_fan_widgets[name] = (info, slider)
                self.print_fan_area.addWidget(frame)
            if not fans:
                self.print_fan_area.addWidget(label('No manual fans detected.', 'caption'))
        for name, (info, slider) in self.print_fan_widgets.items():
            current = round(fval(st.get(name, {}).get('speed')) * 100)
            pending = self._fan_pending.get(name)
            if pending and abs(current - pending[0]) > 1 and time.monotonic() - pending[1] < 4:
                current = pending[0]
            if slider is not None:
                if slider.isSliderDown():
                    info.setText(f'{slider.value()} %')
                    continue
                slider.blockSignals(True)
                slider.setValue(current)
                slider.blockSignals(False)
            info.setText(f'{current} %')

    def print_fan_slider_changed(self, name, value):
        pair = self.print_fan_widgets.get(name)
        if pair is not None:
            pair[0].setText(f'{value} %')

    def print_fan_slider_released(self, name):
        pair = self.print_fan_widgets.get(name)
        if pair is None or pair[1] is None:
            return
        value = pair[1].value()
        if self._fan_last_sent.get(name) == value:
            return
        self._fan_last_sent[name] = value
        self._fan_pending[name] = (value, time.monotonic())
        if name == 'fan':
            command = f'M106 S{round(value * 255 / 100)}'
        else:
            command = f'SET_FAN_SPEED FAN={name.split(" ", 1)[1]} SPEED={value / 100:.3f}'
        self.network.script(command)

    def make_dashboard(self, l):
        root = l
        l = self.layout_block('dashboard_order', root, 'motion', 'Live motion')
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
        l = self.layout_block('dashboard_order', root, 'motion_limits', 'Motion limits / Motors Off')
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
        l = self.layout_block('dashboard_order', root, 'overrides', 'Speed / Flow Overrides')
        l.addWidget(label('Speed / Flow Overrides', 'caption'))
        self.add_override_controls(l)
        l = self.layout_block('dashboard_order', root, 'print_status', 'Print status')
        f, c = card('Druckstatus')
        self.s['print'] = label('Moonraker wird verbunden ...')
        c.addWidget(self.s['print'])
        l.addWidget(f)
        # Stable, compact temperature overview instead of a long ad-hoc text line.
        l = self.layout_block('dashboard_order', root, 'temperatures', 'Temperatures')
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
        l = self.layout_block('dashboard_order', root, 'presets', 'Temperature presets')
        f, c = card('Temperature presets')
        self.dashboard_presets = QGridLayout()
        self.dashboard_presets.setSpacing(6)
        c.addLayout(self.dashboard_presets)
        c.addWidget(button('Edit presets', self.edit_presets))
        c.addWidget(label('Hotend presets use the currently selected tool; bed and chamber are independent.', 'caption'))
        l.addWidget(f)
        self.rebuild_presets()
        l = self.layout_block('dashboard_order', root, 'homing', 'Homing')
        f, c = card('Homing  ·  getrennte Achsen')
        bar = QHBoxLayout()
        for axis in ['X', 'Y', 'Z', 'XY', 'ALLE']:
            cmd = 'G28' if axis == 'ALLE' else 'G28 ' + (axis if axis != 'XY' else 'X Y')
            b = button(axis, lambda checked=False, cmd=cmd: self.request_toolhead_action(cmd))
            b.setProperty('homeLabel', b.text())
            b.setEnabled(False)
            self.dashboard_home_buttons[cmd] = b
            bar.addWidget(b)
        c.addLayout(bar)
        self.s['homed'] = label('Referenzierung: --', 'caption')
        c.addWidget(self.s['homed'])
        self.dashboard_homing_status = label('', 'caption')
        self.dashboard_homing_status.hide()
        c.addWidget(self.dashboard_homing_status)
        l.addWidget(f)

    def make_tools(self, l):
        root = l
        l = self.layout_block('tools_order', root, 'tool', 'Tool / Extruder / PA')
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
        l = self.layout_block('tools_order', root, 'retract', 'Firmware Retract')
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
        l = self.layout_block('tools_order', root, 'extrusion', 'Extrusion')
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
        root = l
        l = self.layout_block('temperatures_order', root, 'heaters', 'Heaters / Sensors')
        l.addWidget(label('Heizer mit Sollwert · Sensoren nur Anzeige', 'caption'))
        self.temp_box, self.temp_content = card()
        self.temp_info = label('Verbindung zu Moonraker wird hergestellt ...')
        self.temp_content.addWidget(self.temp_info)
        self.temperature_grid = QGridLayout()
        self.temperature_grid.setSpacing(7)
        self.temp_content.addLayout(self.temperature_grid)
        self.temp_widgets = {}
        l.addWidget(self.temp_box)
        l = self.layout_block('temperatures_order', root, 'presets', 'Temperature presets')
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
        root = l
        l = self.layout_block('control_order', root, 'jog', 'Manual movement / Jog')
        f, c = card('Toolhead')
        c.addWidget(label('G-code position · mm', 'caption'))
        positions = QGridLayout()
        positions.setSpacing(5)
        self.toolhead_position_labels = {}
        for i, axis in enumerate('XYZ'):
            tile = QVBoxLayout()
            tile.setSpacing(3)
            name = label(axis + ' · —', 'caption')
            value = label('—', 'metricSmall' if self.tiny else 'metric')
            value.setAlignment(Qt.AlignCenter)
            value.setWordWrap(False)
            machine = label('—', 'caption')
            machine.setAlignment(Qt.AlignCenter)
            tile.addWidget(name)
            tile.addWidget(value)
            tile.addWidget(machine)
            positions.addLayout(tile, 0, i)
            positions.setColumnStretch(i, 1)
            self.toolhead_position_labels[axis] = (name, value, machine)
        c.addLayout(positions)
        self.jog_step = 50.0
        self.jog_step_buttons = {}
        self.jog_step_group = QButtonGroup(self)
        self.jog_step_group.setExclusive(True)
        c.addWidget(label('Step · mm', 'caption'))
        steps = QGridLayout()
        steps.setSpacing(4)
        columns = 3 if self.tiny else 5
        for i, step in enumerate(jog_step_values(options('extrusion', 'jog_steps', '1, 5, 15, 25, 50'))):
            b = button(f'{step:g}', lambda checked=False, step=step: self.select_jog_step(step))
            b.setCheckable(True)
            self.jog_step_group.addButton(b)
            self.jog_step_buttons[step] = b
            steps.addWidget(b, i // columns, i % columns)
        for col in range(columns):
            steps.setColumnStretch(col, 1)
        c.addLayout(steps)
        self.select_jog_step(50.0)
        self.toolhead_jog_buttons = {}
        self.toolhead_action_buttons = {}
        movement = QGridLayout()
        movement.setSpacing(5)
        for text, axis, direction, xx, yy in [('↑ Y+', 'Y', 1, 1, 0), ('← X−', 'X', -1, 0, 1),
                                              ('X+ →', 'X', 1, 2, 1), ('↓ Y−', 'Y', -1, 1, 2),
                                              ('↑ Z+', 'Z', 1, 3, 0), ('↓ Z−', 'Z', -1, 3, 2)]:
            b = button(text, partial(self.jog, axis, direction))
            b.setEnabled(False)
            self.toolhead_jog_buttons[(axis, direction)] = b
            movement.addWidget(b, yy, xx)
        for col in range(4):
            movement.setColumnStretch(col, 1)
        homing = QGridLayout()
        homing.setSpacing(5)
        def add_action(text, script, r, col, span=1, axes='', feature=''):
            b = button(text, lambda checked=False: self.request_toolhead_action(script, axes, feature), not bool(homing_axes(script)))
            if homing_axes(script):
                b.setProperty('homeLabel', b.text())
            b.setEnabled(False)
            if feature:
                b.hide()
            homing.addWidget(b, r, col, 1, span)
            self.toolhead_action_buttons[script] = (b, axes, feature)
        add_action('Home all', 'G28', 0, 0, 3)
        for i, axis in enumerate('XYZ'):
            add_action(axis, 'G28 ' + axis, 1, i)
        add_action('Home XY', 'G28 X Y', 2, 0, 3)
        add_action('Z-Tilt', 'Z_TILT_ADJUST', 3, 0, 3, 'xyz', 'z_tilt')
        add_action('QGL', 'QUAD_GANTRY_LEVEL', 4, 0, 3, 'xyz', 'quad_gantry_level')
        controls = QVBoxLayout() if self.tiny or self.width() < 620 else QHBoxLayout()
        controls.setSpacing(7)
        controls.addLayout(movement, 1)
        controls.addLayout(homing, 1)
        c.addLayout(controls)
        self.toolhead_busy_label = label('', 'caption')
        self.toolhead_busy_label.hide()
        c.addWidget(self.toolhead_busy_label)
        self.toolhead_hint = label('Manual movement unavailable', 'caption')
        c.addWidget(self.toolhead_hint)
        control_motor_button = button('Motors Off', partial(self.request_toolhead_action, 'M84', '', ''))
        control_motor_button.setEnabled(False)
        self.toolhead_action_buttons['M84'] = (control_motor_button, '', '')
        c.addWidget(control_motor_button)
        l.addWidget(f)
        l = self.layout_block('control_order', root, 'overrides', 'Speed / Flow Overrides')
        l.addWidget(label('Speed / Flow Overrides', 'caption'))
        self.add_override_controls(l)
        l.addWidget(label('Flow override is global for the G-Code stream, including tool changes.', 'caption'))
        l = self.layout_block('control_order', root, 'fans', 'Fans')
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
        f, c = card('Print Files')
        self.files_status = label('Refresh to load G-code files.', 'caption')
        self.files_status.setTextFormat(Qt.PlainText)
        c.addWidget(self.files_status)
        c.addWidget(button('Refresh files', self.reload_print_files))
        self.file_grid = QGridLayout()
        self.file_grid.setSpacing(6)
        self.file_prev = button('Previous page', partial(self.change_file_page, -1))
        self.file_next = button('Next page', partial(self.change_file_page, 1))
        self.file_page_label = label('—', 'caption')
        self.file_page_label.setAlignment(Qt.AlignCenter)
        c.addLayout(row(self.file_prev, self.file_page_label, self.file_next))
        c.addLayout(self.file_grid)
        self.file_prev.setEnabled(False)
        self.file_next.setEnabled(False)
        l.addWidget(f)

    def reload_print_files(self):
        if self.busy_files:
            return
        self.busy_files = True
        self.files_status.setText('Loading print files…')
        self.network.load_files(None)

    def receive_files(self, entries, error):
        self.busy_files = False
        self._file_entries = print_file_entries(entries) if not error else []
        self._file_page = 0
        self.files_status.setText(('Could not load files: ' + error) if error else f'{len(self._file_entries)} print files · newest first')
        self.render_file_page()

    def change_file_page(self, delta):
        self._file_page = max(0, min(max(0, (len(self._file_entries)-1)//6), self._file_page + delta))
        self.render_file_page()

    def render_file_page(self):
        self._file_generation += 1
        self._clear_layout(self.file_grid)
        self._file_cards = {}
        pages = max(1, (len(self._file_entries)+5)//6)
        self.file_page_label.setText(f'{self._file_page+1} / {pages}')
        self.file_prev.setEnabled(self._file_page > 0)
        self.file_next.setEnabled(self._file_page+1 < pages)
        cols = 1 if self.tiny else 2
        entries = self._file_entries[self._file_page*6:self._file_page*6+6]
        for i, entry in enumerate(entries):
            filename = entry['path']
            panel, c = card()
            panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            image = label('Loading preview…', 'caption')
            image.setAlignment(Qt.AlignCenter)
            image.setFixedHeight(88 if self.tiny else 120)
            image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            c.addWidget(image)
            name = label('', 'readout')
            name.setTextFormat(Qt.PlainText)
            name.setText(posixpath.basename(filename))
            name.setToolTip(filename)
            c.addWidget(name)
            folder = label('', 'caption')
            folder.setTextFormat(Qt.PlainText)
            folder.setText(posixpath.dirname(filename))
            if posixpath.dirname(filename):
                c.addWidget(folder)
            details = label('Estimated time: —', 'caption')
            c.addWidget(details)
            start = button('Start print', partial(self.print_selected, filename), True)
            c.addWidget(start)
            self._file_cards[filename] = (image, details, start)
            self.file_grid.addWidget(panel, i//cols, i%cols)
        self.update_file_controls(self._live_status)
        if self.stack.currentWidget() == self.pages.get('Dateien'):
            self.network.request_file_previews(self._file_generation, list(self._file_cards))

    def receive_file_preview(self, generation, filename, metadata, thumbnail):
        if generation != self._file_generation or filename not in self._file_cards:
            return
        image, details, _ = self._file_cards[filename]
        pix = QPixmap()
        if thumbnail and pix.loadFromData(thumbnail) and pix.width() <= 4096 and pix.height() <= 4096:
            image.setPixmap(pix.scaled(180 if self.tiny else 220, image.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            image.setText(tr('No preview'))
        estimate = fval(metadata.get('estimated_time'), float('nan'))
        details.setText('Estimated time: ' + duration_text(estimate))

    def update_file_controls(self, status):
        allowed = (self.live_print_state() is not None and manual_motion_allowed(status)
                   and not self._file_start_pending and not self._toolhead_pending)
        for _, _, start in self._file_cards.values():
            start.setEnabled(allowed)

    def file_start_result(self, accepted, message):
        self._file_start_finished_at = time.monotonic()
        self._live_status_at = 0.0
        self.files_status.setText(message)
        self.update_file_controls({})
        if not accepted:
            self.record_event('Warning', 'Print start not confirmed', message, popup=True)
        self.network.poll()

    def update_exclusion_controls(self, status):
        supported = isinstance(status.get('exclude_object'), dict)
        entries = exclude_objects(status)
        ready = self.live_print_state() in ('printing', 'paused')
        available = [name for name, excluded, _ in entries if not excluded and valid_exclude_name(name)]
        self.exclude_button.setEnabled(ready and bool(available) and not self._exclude_pending)
        self.exclude_button.setText(tr('Exclude Object') + (f' ({sum(excluded for _, excluded, _ in entries)}/{len(entries)})' if entries else ''))
        if not supported:
            hint = 'Object exclusion requires [exclude_object] and object-marked G-code.'
        elif not entries:
            hint = 'No named objects reported for this print.'
        else:
            hint = f'{len(entries)} objects · {sum(excluded for _, excluded, _ in entries)} excluded'
        self.exclude_hint.setText(hint)
        if self._exclude_dialog is not None:
            ps = status.get('print_stats', {})
            elapsed = fval(ps.get('total_duration'), float('nan'))
            if (not ready or ps.get('filename') != self._exclude_dialog.job
                    or not math.isfinite(elapsed) or elapsed < self._exclude_dialog.last_duration):
                self._exclude_dialog.reject()
            else:
                self._exclude_dialog.last_duration = elapsed
                self.render_exclude_list(status)

    def render_exclude_list(self, status):
        d = self._exclude_dialog
        entries = exclude_objects(status)
        shapes = object_shapes(status)
        bounds, known = object_map_bounds(status, shapes, d.fit_objects)
        d.bed.set_status(shapes, bounds, known)
        mapped = {name for name, *_ in shapes}
        d.map_tab.setEnabled(bool(shapes))
        if not shapes:
            d.views.setCurrentIndex(1)
        d.map_info.setText(tr('XY travel / object area' if known else 'Object area (auto fit)') +
                           f' · {len(mapped)}/{len(entries)} ' + tr('mapped') +
                           (' · ' + tr('Center markers') if any(not poly for _, poly, *_ in shapes) else ''))
        signature = (tuple(entries), tuple(sorted(mapped)))
        if signature == d.signature:
            return
        d.signature = signature
        selected_items = d.objects.selectedItems()
        selected = selected_items[0].data(Qt.UserRole) if selected_items else None
        d.objects.blockSignals(True)
        d.objects.clear()
        for name, excluded, current in entries:
            suffix = ' · EXCLUDED' if excluded else ' · Unsupported name' if not valid_exclude_name(name) else ' · Printing now' if current else ''
            if name not in mapped:
                suffix += ' · List only'
            item = QListWidgetItem(name + suffix)
            item.setData(Qt.UserRole, name)
            if excluded or not valid_exclude_name(name):
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            d.objects.addItem(item)
            if selected == name and item.flags() & Qt.ItemIsSelectable:
                d.objects.setCurrentItem(item)
        d.objects.blockSignals(False)
        d.sync_selection()

    def open_exclude_objects(self):
        if self._exclude_pending or self.live_print_state() not in ('printing', 'paused'):
            return
        ps = self._live_status.get('print_stats', {})
        filename = ps.get('filename')
        duration = fval(ps.get('total_duration'), float('nan'))
        d = QDialog(self)
        d.setWindowTitle(tr('Exclude Object'))
        fit_dialog(d, 650, 460)
        d.job = filename
        d.last_duration = duration
        d.signature = None
        d.fit_objects = False
        c = QVBoxLayout(d)
        c.setContentsMargins(6, 5, 6, 5)
        c.setSpacing(3)
        d.map_tab = button('Bed view', lambda: d.views.setCurrentIndex(0))
        list_tab = button('Object list', lambda: d.views.setCurrentIndex(1))
        def fit_toggle():
            d.fit_objects = not d.fit_objects
            fit_button.setText(tr('Bed / XY' if d.fit_objects else 'Fit objects'))
            self.render_exclude_list(self._live_status)
            if d.map_tab.isEnabled():
                d.views.setCurrentIndex(0)
        fit_button = button('Fit objects', fit_toggle)
        c.addLayout(row(d.map_tab, list_tab, fit_button))
        d.views = QStackedWidget()
        d.bed = ObjectBedView()
        d.views.addWidget(d.bed)
        d.objects = QListWidget()
        d.objects.setWordWrap(True)
        d.objects.setStyleSheet('QListWidget::item { padding: 8px; }')
        QScroller.grabGesture(d.objects.viewport(), QScroller.LeftMouseButtonGesture)
        d.views.addWidget(d.objects)
        c.addWidget(d.views, 1)
        d.map_info = label('', 'caption')
        c.addWidget(d.map_info)
        selection = label('', 'caption')
        selection.setTextFormat(Qt.PlainText)
        selection.setWordWrap(False)
        selection.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        c.addWidget(selection)
        confirm = button('Exclude selected' if self.tiny else 'Exclude selected object', d.accept)
        confirm.setObjectName('danger')
        confirm.setEnabled(False)
        close = button('Close', d.reject)
        for b in (confirm,close,d.map_tab,list_tab,fit_button):
            if self.tiny:
                b.setMinimumHeight(27)
                b.setStyleSheet('QPushButton { min-height: 22px; padding: 2px; }')
        c.addLayout(row(close, confirm))

        def sync_selection():
            items = d.objects.selectedItems()
            item = items[0] if items else None
            name = item.data(Qt.UserRole) if item else None
            d.bed.set_selection(name)
            confirm.setEnabled(bool(name) and bool(item.flags() & Qt.ItemIsSelectable))
            selection.setText(tr('Selected: ') + name if name else tr('Tap to select' if self.tiny else 'Tap an object · Green: printing · Red: excluded'))
            selection.setToolTip(name or '')
        d.sync_selection = sync_selection
        d.objects.itemSelectionChanged.connect(sync_selection)

        def pick(name):
            d.objects.clearSelection()
            for i in range(d.objects.count()):
                item = d.objects.item(i)
                if item.data(Qt.UserRole)==name and item.flags() & Qt.ItemIsSelectable:
                    d.objects.setCurrentItem(item)
                    item.setSelected(True)
                    break
            sync_selection()

        def overlap():
            pick('')
            d.views.setCurrentIndex(1)
            selection.setText(tr('Overlapping objects. Select the exact name in List.'))

        d.bed.picked.connect(pick)
        d.bed.ambiguous.connect(overlap)
        self._exclude_dialog = d
        self.render_exclude_list(self._live_status)
        result = d.exec_()
        items = d.objects.selectedItems()
        name = items[0].data(Qt.UserRole) if items else None
        self._exclude_dialog = None
        duration = d.last_duration
        if result != QDialog.Accepted or not exclusion_allowed(self._live_status, filename, name, duration):
            return
        if QMessageBox.question(self, tr('Exclude Object'), 'Permanently skip this object in the current print?\n\n' + name, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self.live_print_state() not in ('printing', 'paused') or not exclusion_allowed(self._live_status, filename, name, duration):
            return
        self._exclude_pending = True
        self.update_exclusion_controls(self._live_status)
        self.network.exclude_print_object(filename, name, duration)

    def object_exclusion_result(self, accepted, message):
        self._exclude_finished_at = time.monotonic()
        self._live_status_at = 0.0
        self.update_exclusion_controls({})
        self.record_event('Printer' if accepted else 'Warning', 'Object exclusion', message, popup=True)
        self.network.poll()

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
        root = l
        l = self.layout_block('macros_order', root, 'commands', 'Run macros')
        f, c = card('Makros ausführen')
        self.macro_buttons = QGridLayout()
        self.macro_buttons.setSpacing(7)
        c.addLayout(self.macro_buttons)
        l.addWidget(f)
        l = self.layout_block('macros_order', root, 'visibility', 'Macro visibility')
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
        self.setStyleSheet(display_style(STYLE, self.ui_scale))
        # Fan sliders use a custom stylesheet, so refresh it explicitly.
        for _name, (_text, slider) in list(getattr(self, 'fan_widgets', {}).items()) + list(getattr(self, 'print_fan_widgets', {}).items()):
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
        if getattr(self, '_clean_lock', None) is not None or (getattr(self, '_shade', None) is not None and self._shade.isVisible()):
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
        self.notification_text.setTextFormat(Qt.PlainText)
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
                self.record_event('Warning' if text.startswith('!!') else 'G-Code', 'G-Code response', readable, popup=True)

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

    # ---------- Print monitoring ----------
    def live_print_state(self):
        if self._health_state != 'ready' or time.monotonic() - self._live_status_at > 5:
            return None
        return self._live_status.get('print_stats', {}).get('state')

    def receive_print_metadata(self, filename, metadata, thumbnail):
        if filename != self._print_meta_name:
            return
        self._print_meta = metadata
        if thumbnail:
            pix = QPixmap()
            if pix.loadFromData(thumbnail) and pix.width() <= 4096 and pix.height() <= 4096:
                self.print_thumb.setPixmap(pix.scaled(200 if self.tiny else 300, 115 if self.tiny else 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.print_thumb.setText('No preview')
        else:
            self.print_thumb.setText('No preview')

    def update_job_controls(self, status):
        state = status.get('print_stats', {}).get('state')
        ready = self._health_state == 'ready' and time.monotonic() - self._live_status_at <= 5
        for action, b in self.print_actions.items():
            allowed = state in {'pause': ('printing',), 'resume': ('paused',), 'cancel': ('printing', 'paused')}[action]
            b.setEnabled(ready and allowed and not self._print_action_pending)
        gm = status.get('gcode_move', {})
        z = live_z_value(status) if ready else None
        self.print_z_offset.setText('Z offset: ' + (f'{z:+.3f} mm' if z is not None else '—'))
        allowed = ready and state == 'printing' and z is not None and not self._z_pending and not self._z_save_pending and 'z' in status.get('toolhead', {}).get('homed_axes', '')
        for b in self.z_buttons:
            b.setEnabled(allowed)
        self.z_reset_button.setEnabled(allowed and z_single_tool(status) and .0005 <= abs(z) <= .5)
        candidate, reason = z_save_snapshot(status, conf('babystep', 'eddy_method', '').strip().lower()) if ready else (None, 'Printer status unavailable.')
        idle = ready and state in ('standby', 'complete') and host_shutdown_allowed(status)
        self.z_save_button.setText('Save after print' if state in ('printing','paused') else ('Confirm & save Z offset' if self._z_after_ready else 'Save Z offset'))
        self.z_save_button.setEnabled(bool(candidate and (idle or state == 'printing') and not self._z_pending and not self._z_save_pending and not self._z_save_locked))
        self.z_save_hint.setText(('Verify the last save in printer.cfg and restart VeloxScreen before retrying.' if self._z_save_locked else ('Method: ' + candidate['method'] + ' · ' + f"{candidate['z']:+.3f} mm") if candidate else reason))
        self.z_cancel_button.setVisible(self._z_after_print is not None)
        self.z_cancel_button.setEnabled(self._z_after_print is not None and not self._z_save_pending)
        self.update_exclusion_controls(status)
        self.update_file_controls(status)

    def refresh_job(self, status):
        ps = status.get('print_stats', {})
        state = ps.get('state')
        filename = str(ps.get('filename') or '')
        if filename != self._print_meta_name:
            self._print_meta_name = filename
            self._print_meta = {}
            self._metadata_requested = 0.0
            self.print_thumb.setText('No preview')
        if filename and not self._print_meta and (not self._metadata_requested or time.monotonic() - self._metadata_requested > 60):
            self._metadata_requested = time.monotonic()
            self.network.fetch_print_metadata(filename)
        progress = print_progress(status)
        self.print_progress_bar.setValue(round(progress * 1000))
        elapsed = bounded_number(ps.get('print_duration'), 0, 0, 1e9)
        estimate = remaining_seconds(progress, elapsed, self._print_meta.get('estimated_time'))
        if state in ('printing', 'paused'):
            self.remaining_label.setText(duration_text(estimate))
            if state == 'printing' and estimate is not None and math.isfinite(estimate):
                finish = datetime.datetime.fromtimestamp(time.time()+estimate)
                today = datetime.date.today()
                when = finish.strftime('%H:%M')
                if finish.date() != today:
                    when += '\n' + finish.strftime('%d.%m.')
                self.eta_label.setText(when)
                self.print_estimate_info.setText('Estimated · host local time · elapsed ' + duration_text(elapsed))
            else:
                self.eta_label.setText('PAUSED' if state == 'paused' else '—')
                self.print_estimate_info.setText('Paused · estimate excludes pause duration' if state == 'paused' else 'Waiting for sufficient progress or slicer estimate')
        elif state == 'complete':
            self.remaining_label.setText('00h 00m')
            self.eta_label.setText('DONE')
            self.print_estimate_info.setText('Print complete · elapsed ' + duration_text(elapsed))
        else:
            self.remaining_label.setText('—')
            self.eta_label.setText('—')
            self.print_estimate_info.setText('No active print')
        info = ps.get('info') or {}
        current = info.get('current_layer')
        total = info.get('total_layer') or self._print_meta.get('layer_count')
        layer = f'{current if current is not None else "—"} / {total if total else "—"}'
        active = status.get('toolhead', {}).get('extruder') or '—'
        tool = next((name for name, obj in config.items('tools') if obj == active), None) if config.has_section('tools') else None
        if not tool and re.fullmatch(r'extruder\d*', active):
            tool = 'T' + (active[8:] or '0')
        length = fval(ps.get('filament_used'), float('nan'))
        usage = f'{length / 1000:.2f} m' if math.isfinite(length) and length >= 0 else '—'
        density = bounded_number(conf('print_view', 'filament_density', '0'), 0, 0, 30)
        settings = status.get('configfile', {}).get('settings', {})
        diameters = {fval(settings.get(n, {}).get('filament_diameter'), -1) for n in self.all_objects if re.fullmatch(r'extruder\d*', n)}
        if len(diameters) == 1:
            grams = filament_grams(length, next(iter(diameters)), density)
            if grams is not None:
                usage += f' · ~{grams:.1f} g'
        self.print_details.setText(f'Layer: {layer}\nFilament used: {usage}\nActive tool: {tool or active} · {active}')
        self.update_job_controls(status)

    def request_print_action(self, action):
        state = self.live_print_state()
        allowed = {'pause': ('printing',), 'resume': ('paused',), 'cancel': ('printing', 'paused')}
        if self._print_action_pending or state not in allowed.get(action, ()):
            return
        if action == 'cancel' and QMessageBox.question(self, 'Cancel print', 'Cancel the current print? This cannot be undone.', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._print_action_pending = True
        self.update_job_controls(self._live_status)
        self.print_action_status.setText(action.capitalize() + ' requested…')
        self.network.print_action(action)

    def print_action_result(self, action, accepted, message):
        self._print_action_pending = False
        self.print_action_status.setText(message if accepted else 'Request not confirmed: ' + message)
        self.update_job_controls(self._live_status)
        if not accepted:
            self.record_event('Warning', 'Print action not confirmed', message, popup=True)

    def adjust_print_z(self, step):
        if self._z_pending or self.live_print_state() != 'printing' or 'z' not in self._live_status.get('toolhead', {}).get('homed_axes', ''):
            return
        if abs(step) not in Z_STEPS or self._z_save_pending:
            return
        if self.confirm_enabled and QMessageBox.question(self, 'Live Z offset', f'Adjust the live Z offset by {step:+.3f} mm?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._z_pending = True
        self.update_job_controls(self._live_status)
        self.z_action_status.setText('Adjusting…')
        self.network.adjust_live_z(step)

    def live_z_result(self, accepted, message):
        self._z_pending = False
        self._live_status_at = 0.0
        self.z_action_status.setText(message)
        self.update_job_controls({})
        if not accepted:
            self.record_event('Warning', 'Live Z request not confirmed', message, popup=True)

    def reset_live_z(self):
        status = self._live_status
        z = live_z_value(status)
        if self._z_pending or self._z_save_pending or self.live_print_state() != 'printing' or not z_single_tool(status) or z is None or not .0005 <= abs(z) <= .5:
            return
        if QMessageBox.question(self, 'Reset live Z', f'Reset the entire live Z offset from {z:+.3f} mm to zero? The Z axis will move. Verify nozzle clearance.', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.cancel_z_reminder()
        self._z_pending = True
        self.update_job_controls(status)
        self.network.adjust_live_z(0, reset_expected=z)

    def request_z_save(self):
        state = self.live_print_state()
        if state is None or self._z_pending or self._z_save_pending or self._z_save_locked:
            return
        eddy = conf('babystep', 'eddy_method', '').strip().lower()
        snapshot, reason = z_save_snapshot(self._live_status, eddy)
        if not snapshot:
            self.z_action_status.setText(reason)
            return
        if state == 'printing':
            self._z_after_print = snapshot
            self._z_after_ready = False
            self.z_action_status.setText('Reminder armed. Confirm after successful print. No automatic save or restart.')
            self.update_job_controls(self._live_status)
            return
        if state not in ('standby','complete') or not host_shutdown_allowed(self._live_status):
            return
        if self._z_after_ready and not z_snapshot_matches(snapshot, self._z_after_print):
            self.cancel_z_reminder()
            self.z_action_status.setText('Saved reminder no longer matches live offset. Review before saving.')
            return
        message = (f"Apply the entire live Z offset {snapshot['z']:+.3f} mm to {snapshot['method']}?\n\n"
                   + snapshot['command'] + '\nSAVE_CONFIG\n\nKlipper will restart. Verify this is a calibration correction, not a material/tool offset. Do not start another print. Check first layer again after restart.')
        if QMessageBox.question(self, 'Save Z offset permanently', message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._z_after_print = None
        self._z_after_ready = False
        self._z_save_pending = True
        self.update_job_controls(self._live_status)
        self.z_action_status.setText('Requesting calibration save…')
        self.network.save_live_z(snapshot, eddy)

    def cancel_z_reminder(self):
        self._z_after_print = None
        self._z_after_ready = False
        self.z_action_status.setText('Save reminder cleared.')
        self.update_job_controls(self._live_status)

    def check_z_reminder(self, status, previous):
        if self._z_after_print is None:
            return
        state = status.get('print_stats', {}).get('state')
        current, _ = z_save_snapshot(status, conf('babystep', 'eddy_method', '').strip().lower())
        if (not z_snapshot_matches(current, self._z_after_print) or (current and current['duration'] < self._z_after_print['duration']) or state not in ('printing','paused','complete')
                or (state == 'printing' and previous not in ('printing','paused'))):
            self.cancel_z_reminder()
            self.z_action_status.setText('Save reminder cleared: offset, job or calibration changed.')
            self.record_event('Printer', 'Z offset reminder cancelled', 'Offset, job or calibration changed; no configuration was saved.', popup=True)
            return
        if state == 'complete' and not self._z_after_ready:
            if not host_shutdown_allowed(status):
                return
            self._z_after_ready = True
            self.z_action_status.setText('Print complete. Tap Confirm & save Z offset when ready.')
            self.record_event('Printer', 'Z offset ready to save', 'Open Print and confirm the calibration save. Klipper will restart.', popup=True)

    def z_save_result(self, accepted, message):
        self._z_save_pending = False
        self._z_save_locked = accepted or message.startswith('Save result uncertain')
        self._z_save_accepted = accepted
        self._live_status_at = 0.0
        self.z_action_status.setText(message)
        self.update_job_controls({})
        self.record_event('Printer' if accepted else 'Warning', 'Z offset save request' if accepted else 'Z offset save not confirmed', message, popup=True)

    # ---------- Persistent notification center ----------
    def _load_history(self):
        if not flag('notifications', 'persist_history', True):
            return
        try:
            if self._history_path.stat().st_size > 512000:
                return
            records = json.loads(self._history_path.read_text(encoding='utf-8'))
            for item in records[-150:] if isinstance(records, list) else []:
                if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ('time', 'category', 'title', 'message')):
                    self._history.append({k: item[k][:1200] for k in ('time', 'category', 'title', 'message')})
        except (OSError, ValueError, TypeError):
            pass

    def save_history(self):
        if not self._history_dirty or not flag('notifications', 'persist_history', True):
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix='.velox-notifications-', dir=str(self._history_path.parent))
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(list(self._history), handle, ensure_ascii=False)
                os.replace(path, self._history_path)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
            self._history_dirty = False
        except OSError:
            pass

    def record_event(self, category, title, message='', popup=False):
        title, message = str(title)[:160], str(message)[:1000]
        key = (category, title, message)
        now = time.monotonic()
        if now - self._event_seen.get(key, -100) < 5:
            return
        self._event_seen = {k: v for k, v in self._event_seen.items() if now-v < 60}
        self._event_seen[key] = now
        self._history.append({'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                              'category': category, 'title': title, 'message': message})
        self._history_dirty = True
        if hasattr(self, 'history_list') and self.pages.get('Notifications') is self.stack.currentWidget():
            self.render_history()
        if popup and flag('notifications', 'popup_' + category.lower().replace('-', '_'), True):
            self.show_notification(message if category == 'G-Code' and message else title + (': ' + message if message else ''))

    def make_notifications(self, l):
        f, c = card('Notification center')
        self.history_filter = QComboBox()
        for name in ('All', 'Warning', 'Print', 'Heating', 'Filament', 'G-Code', 'Printer'):
            self.history_filter.addItem(name)
        self.history_filter.currentIndexChanged.connect(self.render_history)
        c.addWidget(self.history_filter)
        self.history_list = QListWidget()
        self.history_list.setWordWrap(True)
        self.history_list.setSelectionMode(QAbstractItemView.SingleSelection)
        QScroller.grabGesture(self.history_list.viewport(), QScroller.LeftMouseButtonGesture)
        self.history_list.setMinimumHeight(150 if self.tiny else 220)
        self.history_list.itemClicked.connect(self.show_history_item)
        c.addWidget(self.history_list)
        c.addWidget(label('Tap an entry to read its full message. Last 150 events; stored locally if enabled.', 'caption'))
        c.addWidget(button('Clear history', self.clear_history))
        l.addWidget(f)
        self.render_history()

    def render_history(self, *_):
        if not hasattr(self, 'history_list'):
            return
        filter_name = self.history_filter.currentText()
        self.history_list.clear()
        for entry in reversed(self._history):
            if filter_name != 'All' and entry['category'] != filter_name:
                continue
            item = QListWidgetItem(entry['time'][5:] + ' · ' + entry['category'] + '\n' + entry['title'] + (' · ' + entry['message'][:110] if entry['message'] else ''))
            item.setData(Qt.UserRole, entry)
            if entry['category'] == 'Warning':
                item.setForeground(QColor('#ff8e85'))
            self.history_list.addItem(item)
        if not self.history_list.count():
            self.history_list.addItem('No notifications in this category')

    def show_history_item(self, item):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(entry['title'])
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(entry['time'] + '\n' + entry['category'] + '\n\n' + entry['title'] + '\n\n' + entry['message'])
        text.setReadOnly(True)
        layout.addWidget(text)
        layout.addWidget(button('Close', dialog.accept))
        fit_dialog(dialog, 600, 400)
        dialog.exec_()

    def clear_history(self):
        if QMessageBox.question(self, 'Clear history', 'Delete the local notification history?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._history.clear()
        self._history_dirty = True
        try:
            self._history_path.unlink(missing_ok=True)
        except OSError as exc:
            warn(self, 'Clear history', str(exc))
        self.render_history()
        self.save_history()

    def track_notifications(self, status, previous):
        ps = status.get('print_stats', {})
        state = ps.get('state')
        if previous is not None and previous != state:
            titles = {'printing': 'Print resumed' if previous == 'paused' else 'Print started', 'paused': 'Print paused',
                      'complete': 'Print completed', 'cancelled': 'Print cancelled', 'error': 'Print error'}
            if state in titles:
                detail = str(ps.get('filename') or '')
                if state == 'complete':
                    detail += ' · ' + duration_text(fval(ps.get('print_duration')))
                if state == 'error':
                    detail += ' · ' + str(ps.get('message') or '')
                self.record_event('Warning' if state == 'error' else 'Print', titles[state], detail, popup=True)
        for name in self.all_objects:
            data = status.get(name, {})
            if name == 'heater_bed' or re.fullmatch(r'extruder\d*', name) or name.startswith('heater_generic '):
                target = fval(data.get('target'), float('nan'))
                heat = heater_state(data)
                old = self._heating_previous.get(name)
                if old is not None and heat != 'unknown' and math.isfinite(target):
                    if old[0] != target and target > 0 and state not in ('printing', 'paused'):
                        self.record_event('Heating', 'Heating target changed', f'{name}: {target:g} °C', popup=True)
                    elif old[0] == target and old[1] in ('heating', 'cooling') and heat == 'ready' and target > 0:
                        self.record_event('Heating', 'Temperature reached', f'{name}: {target:g} °C', popup=True)
                self._heating_previous[name] = (target, heat)
            elif name.startswith(('filament_switch_sensor ', 'filament_motion_sensor ')):
                enabled = data.get('enabled') is True
                detected = data.get('filament_detected')
                old = self._sensor_previous.get(name)
                if enabled and detected is False and old != (enabled, detected) and (old is not None or state in ('printing', 'paused')):
                    self.record_event('Filament', 'Filament sensor triggered', name, popup=True)
                self._sensor_previous[name] = (enabled, detected)

    # ---------- Quick actions ----------
    def quick_catalog(self):
        catalog = {'page:Dashboard': 'Home', 'page:Print': 'Print', 'page:Temperaturen': 'Preheat',
                   'cooldown': 'Cooldown', 'home_axes': 'Home axes', 'page:LED Effects': 'Lights', 'page:Makros': 'Macros',
                   'page:Notifications': 'Alerts', 'page:Network': 'Network', 'lock': 'Clean screen'}
        if config.has_section('temperature_presets'):
            for name in config['temperature_presets']:
                catalog['preset:' + name] = 'Preset: ' + name
        for name in sorted(self.macro_commands, key=str.casefold):
            catalog['macro:' + name] = 'Macro: ' + name
        return catalog

    def quick_choices(self):
        default = ['page:Dashboard', 'page:Temperaturen', 'page:Print', 'page:Notifications']
        try:
            value = json.loads(conf('quick_actions', 'items', json.dumps(default)))
            if isinstance(value, list):
                return list(dict.fromkeys(v for v in value if isinstance(v, str)))[:12]
        except (TypeError, ValueError):
            pass
        return default

    def rebuild_quick_bar(self):
        if not hasattr(self, 'quick_layout'):
            return
        self._clear_layout(self.quick_layout)
        catalog = self.quick_catalog()
        for key in self.quick_choices():
            if key not in catalog:
                continue
            title = catalog[key].removeprefix('Macro: ').removeprefix('Preset: ')
            b = button(title, partial(self.run_quick_action, key))
            b.setMinimumHeight(30 if self.tiny else 36)
            b.setToolTip(catalog[key])
            self.quick_layout.addWidget(b)
        self.quick_layout.addStretch()
        self.quick_panel.setMinimumWidth(self.quick_layout.minimumSize().width())
        self.quick_bar.setVisible(flag('quick_actions', 'enabled', True) and self.quick_layout.count() > 1)

    def run_quick_action(self, key):
        if key.startswith('page:'):
            page = key[5:]
            if page in self.pages:
                self.navigate(page)
        elif key == 'lock':
            self.lock_for_cleaning()
        elif key == 'home_axes':
            if self.live_print_state() is None or not host_shutdown_allowed(self._live_status):
                warn(self, 'Home axes', 'Homing shortcut is available only when the printer is confirmed idle.')
                return
            if QMessageBox.question(self, 'Home axes', 'Home all axes (G28)?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes and self.live_print_state() is not None and host_shutdown_allowed(self._live_status):
                self.request_toolhead_action('G28', already_confirmed=True)
        elif key == 'cooldown':
            if not host_shutdown_allowed(self._live_status) or self.live_print_state() is None:
                warn(self, 'Cooldown', 'Cooldown is available only when the printer is confirmed idle.')
                return
            if QMessageBox.question(self, 'Cooldown', 'Turn off all heaters?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes and self.live_print_state() is not None and host_shutdown_allowed(self._live_status):
                self.network.script('TURN_OFF_HEATERS')
        elif key.startswith('macro:'):
            name = key[6:]
            if self._health_state == 'ready' and name in self.macro_commands:
                self.confirm_script(self.macro_commands[name], name)
        elif key.startswith('preset:'):
            name = key[7:]
            if self._health_state == 'ready' and config.has_option('temperature_presets', name):
                self.apply_temp_preset(name, conf('temperature_presets', name))

    def edit_quick_actions(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Quick actions')
        layout = QVBoxLayout(dialog)
        enabled = TouchCheckBox('Show quick action bar')
        enabled.setChecked(flag('quick_actions', 'enabled', True))
        layout.addWidget(enabled)
        layout.addWidget(label('Select up to 12. Use Up / Down to reorder.', 'caption'))
        items = QListWidget()
        selected = self.quick_choices()
        catalog = self.quick_catalog()
        for key in dict.fromkeys(selected + list(catalog)):
            item = QListWidgetItem(catalog.get(key, key + ' (unavailable)'))
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in selected else Qt.Unchecked)
            items.addItem(item)
        QScroller.grabGesture(items.viewport(), QScroller.LeftMouseButtonGesture)
        layout.addWidget(items, 1)
        def move(delta):
            index = items.currentRow()
            if 0 <= index < items.count() and 0 <= index + delta < items.count():
                item = items.takeItem(index)
                items.insertItem(index+delta, item)
                items.setCurrentRow(index+delta)
        layout.addLayout(row(button('Up', partial(move, -1)), button('Down', partial(move, 1))))
        def save():
            chosen = [items.item(i).data(Qt.UserRole) for i in range(items.count()) if items.item(i).checkState() == Qt.Checked]
            if len(chosen) > 12:
                warn(dialog, 'Quick actions', 'Choose at most 12 shortcuts.')
                return
            try:
                set_config('quick_actions', 'items', json.dumps(chosen, ensure_ascii=False))
                set_config('quick_actions', 'enabled', str(enabled.isChecked()).lower())
            except OSError as exc:
                warn(dialog, 'Quick actions', str(exc))
                return
            dialog.accept()
        layout.addLayout(row(button('Cancel', dialog.reject), button('Save', save, True)))
        fit_dialog(dialog, 540, 460)
        if dialog.exec_() == QDialog.Accepted:
            self.rebuild_quick_bar()

    # ---------- Display comfort ----------
    @staticmethod
    def find_backlight():
        root = Path('/sys/class/backlight')
        try:
            for dev in sorted(root.iterdir()):
                try:
                    if os.access(dev / 'brightness', os.W_OK) and int((dev / 'max_brightness').read_text().strip()) > 0:
                        return dev
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError):
            pass
        return None

    def brightness_percent(self):
        try:
            path = self._brightness_path
            return round(100 * int((path/'brightness').read_text()) / int((path/'max_brightness').read_text())) if path else 100
        except (OSError, ValueError, ZeroDivisionError):
            return 100

    def set_brightness(self, percent):
        if self._brightness_path is None:
            return False
        try:
            maximum = int((self._brightness_path/'max_brightness').read_text())
            value = max(1, round(maximum * max(5, min(100, percent)) / 100))
            (self._brightness_path/'brightness').write_text(str(value))
            return True
        except (OSError, ValueError):
            return False

    def make_comfort_settings(self, l):
        f, c = card('Display comfort')
        self.comfort_fields = {}
        for key, title, default, minimum, maximum, decimals in (
                ('dim_after', 'Dim after (seconds; 0 = off)', 120, 0, 7200, 0),
                ('off_after', 'Blank after (seconds; 0 = off)', 300, 0, 14400, 0),
                ('font_scale', 'Font size multiplier (restart UI)', 1, .8, 1.5, 2),
                ('button_scale', 'Button size multiplier (restart UI)', 1, .8, 1.5, 2)):
            spin = TouchSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(decimals)
            spin.setValue(bounded_number(conf('display', key, str(default)), default, minimum, maximum))
            spin.setProperty('touch_title', title)
            c.addWidget(label(title))
            c.addWidget(spin)
            self.comfort_fields[key] = spin
        self.keep_display_checkbox = TouchCheckBox('Keep display on while printing / paused')
        self.keep_display_checkbox.setChecked(flag('display', 'keep_on_print', True))
        c.addWidget(self.keep_display_checkbox)
        self.hardware_idle_checkbox = TouchCheckBox('Physical screen off via backlight / X11 DPMS (if supported)')
        self.hardware_idle_checkbox.setChecked(flag('display', 'hardware_idle', False))
        self.hardware_idle_checkbox.setEnabled(self._brightness_path is not None or self._dpms_available)
        c.addWidget(self.hardware_idle_checkbox)
        c.addWidget(label('Otherwise dimming and blanking use a software overlay. First touch wakes the display. Physical mode is optional and depends on the display driver.', 'caption'))
        c.addWidget(label('Start page'))
        self.start_page_combo = QComboBox()
        for name in self.nav_defaults:
            self.start_page_combo.addItem(tr(name), name)
        self.start_page_combo.setCurrentIndex(max(0, self.start_page_combo.findData(conf('display', 'start_page', 'Dashboard'))))
        c.addWidget(self.start_page_combo)
        c.addWidget(label('Pointer style (restart UI)'))
        self.pointer_combo = QComboBox()
        for title, mode in (('Small dot', 'dot'), ('Hidden pointer', 'hidden'), ('System pointer', 'system')):
            self.pointer_combo.addItem(tr(title), mode)
        self.pointer_combo.setCurrentIndex(max(0, self.pointer_combo.findData(pointer_mode(conf('display', 'cursor_style', 'dot')))))
        c.addWidget(self.pointer_combo)
        if self._brightness_path is not None:
            c.addWidget(label('Hardware brightness (%)'))
            self.brightness_spin = TouchSpinBox()
            self.brightness_spin.setRange(5, 100)
            self.brightness_spin.setDecimals(0)
            self.brightness_spin.setValue(bounded_number(conf('display', 'brightness', str(self.brightness_percent())), self.brightness_percent(), 5, 100))
            c.addWidget(self.brightness_spin)
        else:
            self.brightness_spin = None
            c.addWidget(label('Hardware brightness unavailable to this user/display. Software dim/blank still works.', 'caption'))
        self.filament_density_spin = TouchSpinBox()
        self.filament_density_spin.setDecimals(3)
        self.filament_density_spin.setRange(0, 30)
        self.filament_density_spin.setValue(bounded_number(conf('print_view', 'filament_density', '0'), 0, 0, 30))
        c.addWidget(label('Filament density (g/cm³; 0 = no gram estimate)'))
        c.addWidget(self.filament_density_spin)
        c.addWidget(label('Use only for jobs with the same filament diameter and density on all tools.', 'caption'))
        c.addWidget(button('Save display settings', self.save_comfort_settings, True))
        c.addWidget(button('Lock screen for cleaning', self.lock_for_cleaning))
        c.addWidget(label('Cleaning lock is available only when the printer is confirmed idle.', 'caption'))
        self.comfort_status = label('', 'caption')
        c.addWidget(self.comfort_status)
        l.addWidget(f)
        # History popup categories remain independent from ordinary G-Code confirmation.
        f, c = card('Notification preferences')
        self.event_preferences = {}
        for key, title in (('persist_history', 'Store notification history locally'), ('popup_print', 'Print progress events'),
                           ('popup_heating', 'Temperature events'), ('popup_filament', 'Filament sensor warnings'),
                           ('popup_warning', 'Error / warning popups'), ('popup_printer', 'Printer ready events'), ('popup_g_code', 'G-Code responses')):
            check = TouchCheckBox(title)
            check.setChecked(flag('notifications', key, True))
            c.addWidget(check)
            self.event_preferences[key] = check
        c.addWidget(button('Save notification preferences', self.save_event_preferences))
        l.addWidget(f)

    def save_event_preferences(self):
        try:
            for key, check in self.event_preferences.items():
                set_config('notifications', key, str(check.isChecked()).lower())
            if not flag('notifications', 'persist_history', True):
                self._history_path.unlink(missing_ok=True)
            else:
                self._history_dirty = True
                self.save_history()
        except OSError as exc:
            warn(self, 'Notifications', str(exc))

    def save_comfort_settings(self):
        try:
            for key, spin in self.comfort_fields.items():
                set_config('display', key, f'{spin.value():g}')
            set_config('display', 'keep_on_print', str(self.keep_display_checkbox.isChecked()).lower())
            set_config('display', 'hardware_idle', str(self.hardware_idle_checkbox.isChecked()).lower())
            set_config('display', 'start_page', self.start_page_combo.currentData())
            set_config('display', 'cursor_style', self.pointer_combo.currentData())
            set_config('print_view', 'filament_density', f'{self.filament_density_spin.value():g}')
            if self.brightness_spin is not None:
                brightness = int(self.brightness_spin.value())
                if self.set_brightness(brightness):
                    set_config('display', 'brightness', brightness)
                else:
                    warn(self, 'Brightness', 'Could not set hardware brightness.')
            self.wake_screen()
            self.comfort_status.setText('Saved. Restart VeloxScreen to apply font, button and pointer settings.')
        except OSError as exc:
            warn(self, 'Display comfort', str(exc))

    def dpms_blank(self):
        """Best-effort X11 power management; never change global OS config."""
        if not self._dpms_available or self._dpms_off:
            return
        try:
            result = subprocess.run(['xset', '-q'], capture_output=True, text=True, timeout=.8, env={**os.environ, 'LC_ALL': 'C'})
            if result.returncode or 'DPMS is ' not in result.stdout:
                return
            if 'DPMS is Disabled' in result.stdout:
                result = subprocess.run(['xset', '+dpms'], capture_output=True, timeout=.8)
                if result.returncode:
                    return
                self._dpms_enabled_by_us = True
            result = subprocess.run(['xset', 'dpms', 'force', 'off'], capture_output=True, timeout=.8)
            self._dpms_off = result.returncode == 0
            if not self._dpms_off:
                self.dpms_wake()
        except (OSError, subprocess.SubprocessError):
            self.dpms_wake()

    def dpms_wake(self):
        if not (self._dpms_off or self._dpms_enabled_by_us):
            return
        try:
            subprocess.run(['xset', 'dpms', 'force', 'on'], capture_output=True, timeout=.8)
            if self._dpms_enabled_by_us:
                subprocess.run(['xset', '-dpms'], capture_output=True, timeout=.8)
        except (OSError, subprocess.SubprocessError):
            pass
        self._dpms_off = False
        self._dpms_enabled_by_us = False

    def check_idle_display(self):
        if self._shade is None:
            return
        if self._health_state != 'ready' or self.live_print_state() is None:
            if self._shade.isVisible():
                self.wake_screen()
            self.release_clean_lock()
            return
        if self._clean_lock is not None or QApplication.activeModalWidget() is not None:
            return
        if flag('display', 'keep_on_print', True) and self.live_print_state() in ('printing', 'paused'):
            return
        idle = time.monotonic() - self._last_input
        dim = bounded_number(conf('display', 'dim_after', '120'), 120, 0, 7200)
        off = bounded_number(conf('display', 'off_after', '300'), 300, 0, 14400)
        opacity = 255 if off > 0 and idle >= off else 150 if dim > 0 and idle >= dim else 0
        if not opacity:
            return
        if not self._shade.isVisible() or self._shade.opacity != opacity:
            self.hide_notification()
            self._shade.opacity = opacity
            self._shade.setGeometry(self.rect())
            self._shade.show()
            self._shade.raise_()
            self._shade.update()
            if flag('display', 'hardware_idle', False) and self._brightness_path is not None:
                try:
                    path = self._brightness_path
                    if not hasattr(self, '_backlight_restore') or self._backlight_restore is None:
                        self._backlight_restore = self.brightness_percent()
                        # Save the restore value before turning off the physical backlight.
                        set_config('display', 'brightness', max(5, self._backlight_restore))
                    maximum = int((path/'max_brightness').read_text())
                    (path/'brightness').write_text('0' if opacity == 255 else str(max(1, round(maximum * self._backlight_restore / 100 * .35))))
                except (OSError, ValueError):
                    if opacity == 255:
                        self.dpms_blank()
            elif opacity == 255 and flag('display', 'hardware_idle', False):
                self.dpms_blank()

    def wake_screen(self):
        self._last_input = time.monotonic()
        if self._shade is not None and self._shade.isVisible():
            self._shade.hide()
        self.dpms_wake()
        restore = getattr(self, '_backlight_restore', None)
        if restore is not None and self.set_brightness(max(5, restore)):
            self._backlight_restore = None

    def eventFilter(self, obj, event):
        et = event.type()
        activity = (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.TouchBegin, QEvent.TouchEnd, QEvent.KeyPress, QEvent.Wheel)
        if et in activity:
            self._last_input = time.monotonic()
            if self._shade is not None and self._shade.isVisible():
                # Consume the wake gesture, including the release. Do not click through.
                if et in (QEvent.MouseButtonRelease, QEvent.TouchEnd, QEvent.KeyPress, QEvent.Wheel):
                    self.wake_screen()
                    self._wake_suppress = time.monotonic() + .25
                return True
            if time.monotonic() < self._wake_suppress:
                return True
        return super().eventFilter(obj, event)

    def lock_for_cleaning(self):
        if self._clean_lock is not None or self.live_print_state() is None or not host_shutdown_allowed(self._live_status):
            warn(self, 'Cleaning lock', 'Available only when the printer is confirmed idle.')
            return
        self.wake_screen()
        self.hide_notification()
        dialog = CleaningLock(self)
        self._clean_lock = dialog
        dialog.finished.connect(self.clean_lock_finished)
        dialog.showFullScreen()

    def clean_lock_finished(self, *_):
        dialog = self._clean_lock
        self._clean_lock = None
        if dialog is not None:
            dialog.deleteLater()
        self._last_input = time.monotonic()

    def release_clean_lock(self):
        if self._clean_lock is not None:
            self._clean_lock.accept()


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
        f, c = card('Customize layout')
        c.addWidget(button('Edit layout', self.open_layout_editor, True))
        c.addWidget(label('Reorder blocks, navigation, fans and temperature tiles. Safety controls stay fixed.', 'caption'))
        l.addWidget(f)
        root = l
        l = self.layout_block('settings_order', root, 'comfort', 'Display comfort')
        self.make_comfort_settings(l)
        l = self.layout_block('settings_order', root, 'quick_actions', 'Quick actions')
        f, c = card('Quick actions')
        c.addWidget(button('Choose quick actions', self.edit_quick_actions, True))
        c.addWidget(label('Choose shortcuts and macro favorites for the always-visible bar. Swipe sideways to reach more.', 'caption'))
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'jog', 'Manual movement / Jog')
        f, c = card('Manual movement / Jog')
        c.addWidget(label('Reverse the X, Y or Z jog buttons and set their manual move speeds. '
                          'Only affects movement from VeloxScreen, not homing, printing or Klipper motor direction.',
                          'caption'))
        self.jog_settings_invert = {}
        self.jog_settings_speed = {}
        for axis in ('X', 'Y', 'Z'):
            c.addWidget(label(axis + ' axis', 'caption'))
            reverse = TouchCheckBox('Invert ' + axis + ' jog direction')
            reverse.setChecked(self.jog_invert[axis])
            c.addWidget(reverse)
            self.jog_settings_invert[axis] = reverse
            speed_row = QHBoxLayout()
            speed_row.addWidget(label(axis + ' jog speed (mm/s)'))
            speed = TouchSpinBox()
            speed.setDecimals(1)
            speed.setRange(0.1 if axis == 'Z' else 1.0, 50.0 if axis == 'Z' else 500.0)
            speed.setSingleStep(1.0 if axis == 'Z' else 5.0)
            speed.setValue(self.jog_speed[axis])
            speed.setProperty('touch_title', axis + ' Jog speed (mm/s)')
            speed_row.addWidget(speed)
            c.addLayout(speed_row)
            self.jog_settings_speed[axis] = speed
        c.addWidget(button('Save jog settings', self.save_jog_settings, True))
        self.jog_settings_status = label('', 'caption')
        c.addWidget(self.jog_settings_status)
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'startup', 'Startup screen')
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
        l = self.layout_block('settings_order', root, 'appearance', 'Appearance / theme')
        f, c = card('Appearance / theme')
        c.addWidget(label('Choose any RGB color for accents, panels, backgrounds, sidebar and buttons.'))
        self.theme_preview_label = label('')
        c.addWidget(self.theme_preview_label)
        c.addWidget(button('Edit theme colors', self.edit_theme, True))
        l.addWidget(f)
        self.update_theme_summary()
        l = self.layout_block('settings_order', root, 'language', 'Language')
        f, c = card('Language / Sprache')
        self.lang_combo = QComboBox()
        self.lang_combo.addItem('English', 'en')
        self.lang_combo.addItem('Deutsch', 'de')
        self.lang_combo.setCurrentIndex(0 if LANGUAGE == 'en' else 1)
        c.addWidget(self.lang_combo)
        c.addWidget(label('Default: English. Changing the language restarts the UI.'))
        c.addWidget(button('Apply language', self.apply_language, True))
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'confirmations', 'Confirmation dialogs')
        f, c = card('Bestätigungsdialoge')
        self.confirm_checkbox = TouchCheckBox(tr('Vor G-Code-Befehlen bestätigen'))
        self.confirm_checkbox.setMinimumHeight(46)
        self.confirm_checkbox.setChecked(self.confirm_enabled)
        self.confirm_checkbox.toggled.connect(self.change_confirmation)
        c.addWidget(self.confirm_checkbox)
        c.addWidget(label('Aus = Befehle werden beim Tippen direkt gesendet. Ein = Nachfrage vor jedem Befehl. Einstellung bleibt gespeichert.', 'caption'))
        self.confirm_shutdown_checkbox = TouchCheckBox(tr('Confirm host shutdown'))
        self.confirm_shutdown_checkbox.setMinimumHeight(40)
        self.confirm_shutdown_checkbox.setChecked(self.confirm_shutdown)
        self.confirm_shutdown_checkbox.toggled.connect(self.change_shutdown_confirmation)
        c.addWidget(self.confirm_shutdown_checkbox)
        c.addWidget(label('If disabled, the Settings shutdown button sends the request without asking. Active and paused prints remain protected.', 'caption'))
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'notifications', 'G-Code notifications')
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
        l = self.layout_block('settings_order', root, 'recovery', 'Klipper / Host Recovery')
        f, c = card('Klipper / Host Recovery')
        c.addWidget(label('Use after an emergency stop, MCU shutdown or Klipper error. '
                          'Each action asks for confirmation. These actions will not resume a print.', 'caption'))
        c.addWidget(button('Open Recovery Controls', self.open_recovery_menu, True))
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'service', 'Service Control')
        f, c = card('Service Control')
        c.addWidget(button('VeloxScreen neu starten', self.restart_screen, True))
        self.shutdown_button = button('Shutdown Host', self.request_host_shutdown)
        self.shutdown_button.setToolTip(tr('Shutdown is allowed only when the printer is confirmed idle. Let the hotends cool first.'))
        self.shutdown_button.setEnabled(False)
        c.addWidget(self.shutdown_button)
        self.shutdown_status = label('', 'caption')
        self.shutdown_status.setTextFormat(Qt.PlainText)
        self.shutdown_status.hide()
        c.addWidget(self.shutdown_status)
        c.addWidget(label('Neustart direkt auf dem Touchdisplay. Alternativ über Mainsail → Service Control → veloxscreen (nach einmaliger Einrichtung).', 'caption'))
        l.addWidget(f)
        l = self.layout_block('settings_order', root, 'configuration', 'Configuration')
        f, c = card('Konfiguration')
        c.addWidget(label(str(CFG_PATH), 'caption'))
        c.addWidget(label('Makros ein-/ausblenden unter Makros. Die Einstellungen werden in veloxscreen.conf gespeichert.', 'caption'))
        l.addWidget(f)

    def save_jog_settings(self):
        values = {axis: (self.jog_settings_invert[axis].isChecked(),
                         self.jog_settings_speed[axis].value()) for axis in ('X', 'Y', 'Z')}
        # Single atomic write. Only change live UI behavior once saved.
        if not config.has_section('jog'):
            config.add_section('jog')
        for axis, (inverted, speed) in values.items():
            config.set('jog', 'invert_' + axis.lower(), 'true' if inverted else 'false')
            config.set('jog', 'speed_' + axis.lower(), f'{speed:g}')
        try:
            save_config()
        except OSError as exc:
            self.jog_settings_status.setText('Could not save jog settings.')
            warn(self, 'Jog settings', str(exc))
            return
        self.jog_invert = {axis: inverted for axis, (inverted, _) in values.items()}
        self.jog_speed = {axis: speed for axis, (_, speed) in values.items()}
        self.jog_settings_status.setText('Jog settings saved. Applies immediately.')

    def change_shutdown_confirmation(self, checked):
        previous = self.confirm_shutdown
        try:
            set_config('behavior', 'confirm_shutdown', 'true' if checked else 'false')
        except OSError as exc:
            # Restore the in-memory config too, so a later unrelated save cannot persist a failed edit.
            config.set('behavior', 'confirm_shutdown', 'true' if previous else 'false')
            self.confirm_shutdown_checkbox.blockSignals(True)
            self.confirm_shutdown_checkbox.setChecked(previous)
            self.confirm_shutdown_checkbox.blockSignals(False)
            warn(self, 'Confirm host shutdown', str(exc))
            return
        self.confirm_shutdown = bool(checked)

    def update_shutdown_button(self):
        self.shutdown_button.setEnabled(self._shutdown_idle and not self._shutdown_pending)

    def request_host_shutdown(self):
        if self._shutdown_pending:
            return
        if not self._shutdown_idle:
            warn(self, 'Shutdown Host', 'Shutdown blocked: printer is not confirmed idle.')
            return
        if self.confirm_shutdown:
            if QMessageBox.question(self, tr('Shutdown Host'),
                                    tr('The host will shut down. Let the hotends cool first. Mainsail and SSH will disconnect. Continue?'),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        # Status can change while the modal confirmation is open. The worker also checks fresh data.
        if not self._shutdown_idle or self._shutdown_pending:
            return
        self._shutdown_pending = True
        self.update_shutdown_button()
        self.shutdown_status.setText(tr('Requesting host shutdown...'))
        self.shutdown_status.show()
        self.network.shutdown_host()

    def show_shutdown_result(self, accepted, message):
        self.shutdown_status.setText(tr(message))
        self.shutdown_status.show()
        self.log(message)
        if not accepted:
            self._shutdown_pending = False
            self.update_shutdown_button()
            warn(self, 'Shutdown Host', message)
        # On acceptance, leave disabled; the host should now be shutting down.

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
            if hasattr(self, 'quick_layout'):
                self.rebuild_quick_bar()

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
        names = ordered_items(sorted(self.macro_commands, key=str.casefold), read_layout_order('macro_order'))
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
        self.save_history()
        self.wake_screen()
        # Replaces this process, preserving X11's client PID (no systemd/root needed).
        os.execv(sys.executable, [sys.executable, str(BASE / 'main.py')])

    def error(self, msg):
        self._live_status_at = 0.0
        if self._z_after_print is not None:
            self.cancel_z_reminder()
        self._heating_previous.clear()
        self._sensor_previous.clear()
        self.update_job_controls({})
        self.update_toolhead_control({})
        self.remaining_label.setText('—')
        self.eta_label.setText('—')
        self.print_estimate_info.setText('Status unavailable')
        self.wake_screen()
        self.release_clean_lock()
        self.connection.setText(tr('● Verbindung gestört'))
        self._shutdown_idle = False
        self.update_shutdown_button()
        self._print_temp_live = False
        self.clear_heat_status()
        self.log('FEHLER: ' + msg)
        self.record_event('Warning', 'Request / connection error', msg, popup=True)

    def response(self, msg):
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
        self._live_status = st
        self._live_status_at = time.monotonic()
        for prefix in ('_file_start', '_exclude'):
            finished = getattr(self, prefix + '_finished_at')
            if finished and data.get('poll_started', 0) >= finished:
                setattr(self, prefix + '_pending', False)
                setattr(self, prefix + '_finished_at', 0.0)
        # Never use a poll begun before command completion as the final homed state.
        if self._toolhead_finished_at and not self._toolhead_uncertain and data.get('poll_started', 0) >= self._toolhead_finished_at:
            self._toolhead_pending = ''
            self._toolhead_finished_at = 0.0
            self._homing_timer.stop()
        if self._z_save_locked and self._z_save_accepted and live_z_value(st) == 0 and st.get('configfile', {}).get('save_config_pending') is False:
            self._z_save_locked = False
        self._shutdown_idle = self._health_state == 'ready' and host_shutdown_allowed(st)
        self.update_shutdown_button()
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
        state = ps.get('state', 'standby')
        previous = self._previous_print_state
        self._previous_print_state = state
        self.check_z_reminder(st, previous)
        if previous in ('printing', 'paused') and state in ('complete', 'cancelled', 'error'):
            self.wake_screen()
        if state in ('printing', 'paused'):
            self.release_clean_lock()
            if flag('display', 'keep_on_print', True) and self._shade is not None and self._shade.isVisible():
                self.wake_screen()
        if state == 'printing' and previous not in ('printing', 'paused') and QApplication.activeModalWidget() is None and (
                conf('print_view', 'auto_open', 'true').strip().lower() in ('true', '1', 'yes', 'on')):
            self.navigate('Print')
        self.refresh_job(st)
        self.track_notifications(st, previous)
        progress = print_progress(st) * 100
        self.print_status_label.setText(str(ps.get('filename') or 'No file') + '  ·  ' + str(state))
        self.print_progress_label.setText(f'{progress:.0f} %')
        for key, value in (('speed', fmt(motion.get('live_velocity'))),
                           ('flow', fmt(abs(flow))),
                           ('accel', fmt(toolhead.get('max_accel'), 0))):
            self.print_metrics[key].setText(value)
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
        self.update_toolhead_control(st)
        self.update_dashboard_temperatures(st)
        self.update_discovered_tools(st)
        self.update_temps(st)
        self.update_print_temperatures(st)
        self._temperature_status = st
        self.update_preset_highlights(st)
        self.update_fans(st)
        self.update_print_fans(st)
        self.update_led_effects(st)
        self.update_calib(st)
        self.temp_history.sample(st)

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
        chosen = ordered_items(chosen[:4], read_layout_order('temperature_order'))
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
                self.show_heater_target(subtitle, entry)
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
        self.update_preset_highlights(self._temperature_status)

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

    def resolve_preset_targets(self, string, st):
        """Use identical tool/heater selection for commands and live preset highlighting."""
        requested = {}
        for p in string.split(','):
            if ':' in p:
                k, v = p.strip().split(':',1)
                try:
                    requested[k.strip().lower()] = float(v.strip())
                except ValueError:
                    pass
        heaters = [n for n in self.all_objects if n == 'extruder' or re.fullmatch(r'extruder\d+',n)
                   or n == 'heater_bed' or n.startswith('heater_generic ')]
        active = st.get('toolhead', {}).get('extruder', 'extruder')
        targets = {}
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
            if math.isfinite(value) and 0 <= value < maximum:
                targets[heater] = value
        return targets

    def apply_temp_preset(self, name, string):
        targets = self.resolve_preset_targets(string, self.s)
        commands = [f'SET_HEATER_TEMPERATURE HEATER={heater.split(" ",1)[-1] if heater.startswith("heater_generic ") else heater} TARGET={value:g}'
                    for heater, value in targets.items()]
        if commands:
            self.confirm_script('\n'.join(commands), 'Temperatur-Preset '+name)
        else:
            inform(self, 'Preset', 'Keine passenden regelbaren Heizungen für dieses Preset gefunden.')

    def update_print_temperatures(self, st):
        """Show all controllable heaters and chamber sensors on the Print page."""
        heaters = sorted({n for n in self.all_objects if n == 'extruder' or
                          re.fullmatch(r'extruder\d+', n) or n == 'heater_bed' or
                          n.startswith('heater_generic ')},
                         key=lambda n: (0, 0) if n == 'extruder' else
                         (0, int(n[8:])) if re.fullmatch(r'extruder\d+', n) else
                         (1, 0) if n == 'heater_bed' else (2, n))
        sensors = sorted({n for n in self.all_objects
                          if n.startswith(('temperature_sensor ', 'temperature_fan '))
                          and any(word in n.lower() for word in ('chamber', 'kammer'))})
        names = ordered_items(heaters + sensors, read_layout_order('temperature_order'))
        signature = (tuple(names), tuple(heaters), tuple(sensors))
        if signature != self._print_temp_signature:
            self._print_temp_signature = signature
            self.print_temp_widgets = {}
            while self.print_temp_grid.count():
                item = self.print_temp_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            cols = 1 if self.compact else 2
            for i, name in enumerate(names):
                title = ('T0 · Hotend' if name == 'extruder' else
                         'T' + name[8:] + ' · Hotend' if re.fullmatch(r'extruder\d+', name) else
                         'Heizbett' if name == 'heater_bed' else name.split(' ', 1)[-1])
                tile, lay = card()
                lay.setContentsMargins(6, 4, 6, 4)
                lay.setSpacing(3)
                lay.addWidget(label(title))
                reading = label('-- °C', 'metric')
                reading.setWordWrap(False)
                line = QHBoxLayout()
                line.addWidget(reading, 1)
                target = None
                if name in heaters:
                    target = button('Target -- °C', partial(self.set_print_heater, name), True)
                    target.setEnabled(False)
                    line.addWidget(target, 1)
                else:
                    line.addWidget(label('Sensor only', 'caption'))
                lay.addLayout(line)
                self.print_temp_widgets[name] = (reading, target)
                self.print_temp_grid.addWidget(tile, i // cols, i % cols)
        self._print_temp_live = True
        self.print_temp_info.setVisible(not bool(heaters or sensors))
        if not (heaters or sensors):
            self.print_temp_info.setText(tr('No heaters or chamber sensors found.'))
        for name, (reading, target) in self.print_temp_widgets.items():
            data = st.get(name, {})
            reading.setText(fmt(data.get('temperature')) + ' °C')
            if target is not None:
                self.show_heater_target(target, data, editable=True)
                maximum = fval(st.get('configfile', {}).get('settings', {}).get(name, {}).get('max_temp'), 0)
                target.setEnabled(data.get('target') is not None and math.isfinite(maximum) and maximum > 1)

    def set_print_heater(self, heater):
        """Reuse the existing touch keypad and heater command, only with live limits."""
        if not self._print_temp_live or heater not in self.all_objects:
            return
        cfg = self.s.get('configfile', {}).get('settings', {}).get(heater, {})
        maximum = fval(cfg.get('max_temp'), 0)
        if not math.isfinite(maximum) or maximum <= 1:
            warn(self, 'Print temperatures', 'Heater temperature limit is unavailable.')
            return
        self.set_heater(heater)

    def update_temps(self, st):
        heaters = [x for x in self.all_objects if x == 'extruder' or re.fullmatch(r'extruder\d+',x)
                   or x == 'heater_bed' or x.startswith('heater_generic ')]
        sensors = [x for x in self.all_objects if x.startswith(('temperature_sensor ','temperature_fan '))]
        names = ordered_items(heaters + sensors, read_layout_order('temperature_order'))
        signature = (tuple(names), tuple(heaters), tuple(sensors))
        if signature != self._last_temp_signature:
            self._last_temp_signature=signature
            self.temp_widgets={}
            while self.temperature_grid.count():
                item=self.temperature_grid.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            for i,name in enumerate(names):
                is_heater=name in heaters
                f,c=card()
                pretty=name.replace('temperature_sensor ','').replace('heater_generic ','').replace('temperature_fan ','')
                if name=='heater_bed': pretty='Heizbett'
                if name=='extruder': pretty='T0 · Hotend'
                if re.fullmatch(r'extruder\d+',name): pretty='T'+name[len('extruder'):]+' · Hotend'
                name_label=label(pretty, 'sectionTitle')
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
                self.show_heater_target(target, data)
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
        fans = ordered_items(sorted(fans), read_layout_order('fan_order'))
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

    def select_jog_step(self, step):
        if step not in self.jog_step_buttons:
            return
        self.jog_step = step
        for value, b in self.jog_step_buttons.items():
            b.setChecked(value == step)
            b.setStyleSheet('QPushButton { background: #2866bd; color: white; border-color: #75c3ff; }' if value == step else '')

    def set_home_color(self, button_widget, state):
        if button_widget.property('homeColor') == state:
            return
        button_widget.setProperty('homeColor', state)
        color, border = ('#2866bd', '#5596e8') if state == 'homed' else ('#424952', '#606a77')
        button_widget.setStyleSheet(f'QPushButton {{ background: {color}; color: #ffffff; border: 1px solid {border}; }} '
                                   f'QPushButton:disabled {{ background: {color}; color: #b0b8c3; }} '
                                   'QPushButton:pressed { background: #365c87; }')

    def animate_toolhead_busy(self):
        glyph = ''
        if self._toolhead_pending:
            if self._toolhead_finished_at:
                glyph = '…'
                text = tr('Waiting for live axis status') if not self._toolhead_uncertain else tr('Check printer and restart VeloxScreen before further manual motion.')
            else:
                glyph = ('◐', '◓', '◑', '◒')[self._homing_frame % 4]
                self._homing_frame += 1
                text = glyph + ' ' + tr('Homing in progress' if homing_axes(self._toolhead_pending) else 'Motion request in progress') + ' · ' + self._toolhead_pending
            if self._toolhead_feedback:
                text += ' · ' + self._toolhead_feedback
        else:
            text = self._toolhead_feedback
        for script, widget in list(self.dashboard_home_buttons.items()) + [(key, item[0]) for key, item in self.toolhead_action_buttons.items() if homing_axes(key)]:
            base = widget.property('homeLabel')
            if base:
                text_on_button = glyph + ' ' + base if script == self._toolhead_pending and glyph else base
                if widget.text() != text_on_button:
                    widget.setText(text_on_button)
        for widget in (self.toolhead_busy_label, self.dashboard_homing_status):
            widget.setText(text)
            widget.setVisible(bool(text))

    def update_toolhead_control(self, status):
        ready = self._health_state == 'ready' and self.live_print_state() is not None
        busy = bool(self._toolhead_pending)
        visible_status = status if ready else {}
        coords = toolhead_coordinates(visible_status)
        for axis, (name, value, machine) in self.toolhead_position_labels.items():
            pos, physical, homed = coords[axis]
            digits = 3 if axis == 'Z' else 2
            name.setText(axis + ' · ' + tr('Homed' if homed else 'Not homed') if ready else axis + ' · —')
            value.setText(fmt(pos, digits) if pos is not None else '—')
            machine.setText(tr('Machine') + ' ' + fmt(physical, digits) if physical is not None else '—')
        for (axis, _), b in self.toolhead_jog_buttons.items():
            b.setEnabled(ready and not busy and manual_motion_allowed(status, axis.lower()))
        for script, (b, axes, feature) in self.toolhead_action_buttons.items():
            supported = not feature or feature in self.all_objects
            b.setVisible(supported)
            b.setEnabled(ready and not busy and supported and manual_motion_allowed(status, axes))
            if homing_axes(script):
                self.set_home_color(b, homing_visual_state(script, status, ready, busy))
        for script, b in self.dashboard_home_buttons.items():
            b.setEnabled(ready and not busy and manual_motion_allowed(status))
            self.set_home_color(b, homing_visual_state(script, status, ready, busy))
        self.toolhead_hint.setText(tr('Manual movement requires an idle printer. Home axes before jogging.' if ready and not busy and manual_motion_allowed(status) else 'Manual movement unavailable'))
        self.animate_toolhead_busy()

    def toolhead_motion_result(self, script, accepted, message, uncertain):
        if script != self._toolhead_pending:
            return
        self._toolhead_finished_at = time.monotonic()
        self._toolhead_uncertain = uncertain
        if not accepted:
            self._toolhead_feedback = tr('Motion not confirmed') + ': ' + message
            self.log(self._toolhead_feedback)
            self.record_event('Warning', 'Toolhead motion not confirmed', message, popup=True)
        self._homing_timer.stop()
        self.animate_toolhead_busy()
        QTimer.singleShot(0, self.network.poll)

    def request_toolhead_action(self, script, axes='', feature='', already_confirmed=False):
        if self._toolhead_pending or self.live_print_state() is None or not manual_motion_allowed(self._live_status, axes):
            warn(self, 'Toolhead', 'Manual movement unavailable')
            return
        if feature and feature not in self.all_objects:
            return
        if self.confirm_enabled and not already_confirmed:
            if QMessageBox.question(self, tr('Toolhead'), script + '?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        # A modal confirmation can outlive the idle state.
        if self._toolhead_pending or self.live_print_state() is None or not manual_motion_allowed(self._live_status, axes):
            return
        self._toolhead_pending = script
        self._toolhead_uncertain = False
        self._toolhead_finished_at = 0.0
        self._toolhead_feedback = ''
        self._homing_frame = 0
        self.update_toolhead_control(self._live_status)
        self._homing_timer.start()
        self.network.manual_motion(script, axes, feature, tracked=True)
        self.log('>> ' + script)

    def jog(self, axis, direction):
        if self._toolhead_pending or self.live_print_state() is None or not manual_motion_allowed(self._live_status, axis.lower()):
            warn(self, 'Toolhead', 'Manual movement unavailable')
            return
        dist = self.jog_step * direction
        if self.jog_invert.get(axis, False):
            dist = -dist
        # G-code F is mm/min; UI displays mm/s.
        feed = round(self.jog_speed.get(axis, 10.0 if axis == 'Z' else 100.0) * 60, 3)
        if not math.isfinite(dist) or not math.isfinite(feed) or not dist or feed <= 0:
            return
        self.network.manual_motion(f'SAVE_GCODE_STATE NAME=VELOX_JOG\nG91\nG1 {axis}{dist:g} F{feed:g}\nRESTORE_GCODE_STATE NAME=VELOX_JOG', axis.lower())
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

    def print_selected(self, filename):
        if (not valid_print_path(filename) or self._file_start_pending or self._toolhead_pending
                or self.live_print_state() is None or not manual_motion_allowed(self._live_status)):
            return
        if QMessageBox.question(self, tr('Start print'), 'Start printing this file?\n\n' + filename,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self._file_start_pending or self.live_print_state() is None or not manual_motion_allowed(self._live_status) or self._toolhead_pending:
            return
        self._file_start_pending = True
        self.update_file_controls(self._live_status)
        self.network.start_file(filename)

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
    install_pointer(app, conf('display', 'cursor_style', 'dot'))
    screen = app.primaryScreen()
    geometry = screen.availableGeometry() if screen is not None else None
    _, _, scale = display_profile(geometry.width() if geometry is not None else 800,
                                  geometry.height() if geometry is not None else 480,
                                  conf('display', 'ui_scale', 'auto'))
    app.setFont(QFont('DejaVu Sans', max(9, round(bounded_number(conf('appearance', 'font_size', '12'), 12, 9, 32) * scale * bounded_number(conf('display', 'font_scale', '1'), 1, .8, 1.5)))))
    win = Screen()
    app.aboutToQuit.connect(win.save_history)
    app.aboutToQuit.connect(win.wake_screen)
    if '--windowed' in sys.argv:
        win.show()
    else:
        win.showFullScreen()
    # Start the overlay only once the native window has appeared on the display.
    QTimer.singleShot(0, win.show_startup_splash)
    sys.exit(app.exec_())
