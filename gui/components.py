"""Shared desktop widgets and bounded background image previews."""
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QThread, QSize, QSettings, QTimer
from PySide6.QtGui import QImageReader, QIcon, QPixmap, QColor, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QGraphicsRectItem, QGraphicsItem)
from .image_viewer import ImageViewer
from .styles import STYLE

COLORS = {"Passed": "#13843b", "Defective": "#dc2638", "Review required": "#ad7400",
          "Failed": "#dc2638", "Waiting": "#718096", "Cancelled": "#718096", "Processing": "#008ce8"}
SYMBOLS = {"Passed": "✓", "Defective": "!", "Review required": "!", "Failed": "×",
           "Waiting": "◷", "Cancelled": "–", "Processing": "◉"}
COLORS['Stale'] = '#ad7400'
SYMBOLS['Stale'] = '↻'


class RotationControls(QWidget):
    """Clockwise source corrections; None restores automatic detection."""
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.left = button('↶ Rotate left', lambda: self.changed.emit(-90))
        self.right = button('↷ Rotate right', lambda: self.changed.emit(90))
        self.auto = button('Auto / reset', lambda: self.changed.emit(None))
        self.left.setToolTip('Rotate 90° counterclockwise')
        self.right.setToolTip('Rotate 90° clockwise')
        self.auto.setToolTip('Restore automatic orientation on the next inspection')
        for widget in (self.left, self.right, self.auto):
            layout.addWidget(widget)


def button(text, callback, primary=False):
    b = QPushButton(text)
    b.clicked.connect(callback)
    if primary:
        b.setObjectName("primary")
    return b


def panel(title, subtitle=""):
    w = QWidget()
    w.setObjectName("panel")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(18, 16, 18, 16)
    label = QLabel(title)
    label.setObjectName("sectionTitle")
    layout.addWidget(label)
    if subtitle:
        note = QLabel(subtitle)
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
    return w, layout


class DesktopWindow(QMainWindow):
    def __init__(self, key, title, subtitle=""):
        super().__init__()
        self.key = key
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.prefs = QSettings("PrintInspection", "Desktop")
        central = QWidget()
        self.setCentralWidget(central)
        self.root = QVBoxLayout(central)
        self.root.setContentsMargins(10, 0, 10, 0)
        self.root.setSpacing(12)
        header = QWidget()
        header.setObjectName("header")
        self.header = QHBoxLayout(header)
        self.header.setContentsMargins(28, 16, 28, 16)
        titles = QVBoxLayout()
        self.heading = QLabel(title)
        self.heading.setObjectName("heading")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        self.header.addLayout(titles, 1)
        self.root.addWidget(header)
        geometry = self.prefs.value(key + "/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1380, 860)
        self.bound_to_screen()

    def bound_to_screen(self):
        available = self.screen().availableGeometry()
        self.resize(min(self.width(), available.width()), min(self.height(), available.height() - 35))
        self.move(max(available.left(), min(self.x(), available.right() - self.width())),
                  max(available.top(), min(self.y(), available.bottom() - self.height() - 30)))

    def remember(self):
        self.prefs.setValue(self.key + "/geometry", self.saveGeometry())
        for name in ("splitter", "body"):
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "saveState"):
                self.prefs.setValue(self.key + "/" + name, w.saveState())

    def restore_splitters(self):
        for name in ("splitter", "body"):
            w = getattr(self, name, None)
            state = self.prefs.value(self.key + "/" + name)
            if state and w is not None:
                w.restoreState(state)

    def closeEvent(self, event):
        self.remember()
        super().closeEvent(event)


class Task(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.completed.emit(self.action())
        except Exception as exc:
            self.failed.emit(str(exc))


class ThumbnailWorker(QThread):
    ready = Signal(str, object, str)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        for path in self.paths:
            if self.isInterruptionRequested():
                return
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.width() * size.height() > 40_000_000:
                self.ready.emit(path, None, "Image exceeds pixel limit")
                continue
            reader.setScaledSize(size.scaled(90, 100, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                try:
                    from PIL import Image, ImageOps
                    from PIL.ImageQt import ImageQt
                    with Image.open(path) as source:
                        if source.width * source.height > 40_000_000 or getattr(source, "n_frames", 1) != 1:
                            raise ValueError("Unsupported image size or multiple pages")
                        size = QSize(*source.size)
                        source = ImageOps.exif_transpose(source)
                        source.thumbnail((90, 100))
                        image = ImageQt(source.convert("RGBA")).copy()
                except (OSError, ValueError):
                    self.ready.emit(path, None, "Image cannot be decoded")
                    continue
            try:
                meta = f"{size.width()} × {size.height()}  ·  {Path(path).stat().st_size // 1024} KB"
            except OSError:
                meta = "Image unavailable"
            self.ready.emit(path, image, meta)


class SampleList(QListWidget):
    validated = Signal()
    def __init__(self):
        super().__init__()
        self.setIconSize(QSize(66, 80))
        self.setMinimumWidth(155)
        self.setSpacing(4)
        self.jobs = []
        self.items = {}
        self.valid_paths = set()

    def populate(self, samples):
        for job in self.jobs:
            job.requestInterruption()
        self.clear()
        self.items = {}
        self.valid_paths = set()
        for sample in samples:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sample)
            item.setSizeHint(QSize(190, 96))
            self.addItem(item)
            self.items[sample["path"]] = item
            self.update_item(item)
        job = ThumbnailWorker(list(self.items), self)
        job.ready.connect(self.thumbnail)
        job.finished.connect(lambda: self.finish_job(job))
        self.jobs.append(job)
        job.start()

    def finish_job(self, job):
        self.jobs.remove(job)
        job.deleteLater()

    def thumbnail(self, path, image, meta):
        item = self.items.get(path)
        if item:
            if image is not None and not image.isNull():
                item.setIcon(QIcon(QPixmap.fromImage(image)))
                self.valid_paths.add(path)
            else:
                sample = item.data(Qt.ItemDataRole.UserRole)
                if sample.get("state") == "Ready":
                    sample["state"] = "Failed"
                    item.setData(Qt.ItemDataRole.UserRole, sample)
            item.setToolTip(path + "\n" + meta)
            item.setData(Qt.ItemDataRole.UserRole + 1, meta)
            self.update_item(item)
            self.validated.emit()

    def update_item(self, item):
        sample = item.data(Qt.ItemDataRole.UserRole)
        state = sample.get("state", "Ready")
        count = len((sample.get("result") or {}).get("defects", []))
        item.setText(f"{sample['filename']}\n{SYMBOLS.get(state, '•')}  {state}" + (f" · {count} defects" if sample.get("result") and not sample.get('stale') else ""))
        if state == "Ready" and item.data(Qt.ItemDataRole.UserRole + 1):
            item.setText(item.text() + "\n" + item.data(Qt.ItemDataRole.UserRole + 1))
        item.setForeground(QColor(COLORS.get(state, "#173252")))

    def refresh(self, samples):
        for index, sample in enumerate(samples):
            item = self.item(index)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, sample)
                self.update_item(item)


class Marker(QGraphicsRectItem):
    def __init__(self, row, scale, callback):
        x, y, w, h = row["bbox"]
        super().__init__(x / scale, y / scale, w / scale, h / scale)
        self.row = row
        self.callback = callback
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(3)
        color = QColor("#cf8800" if row["state"] == "Review required" else "#ed2439")
        self.pen_style = QPen(color, 2)
        self.pen_style.setCosmetic(True)
        if row["state"] == "Review required":
            self.pen_style.setStyle(Qt.PenStyle.DashLine)
        self.setPen(self.pen_style)
        self.setToolTip(f"{row['id']} · {row['type']} · {row['state']}")

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.callback(self.row["id"])


class InspectionViewer(ImageViewer):
    marker_clicked = Signal(str)

    def __init__(self, title=""):
        super().__init__(title)
        self.markers = {}
        self.labels = []
        self.beam = None
        self.phase = 0
        self.scan_requested = False
        self.pending_rows = []
        self.markers_visible = True
        self.pending_selection = None
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.animate)
        self.loaded.connect(self.image_ready)

    def image_ready(self):
        self.set_markers(self.pending_rows, self.markers_visible)
        if self.scan_requested:
            self.start_scan()
        if self.pending_selection:
            self.select_marker(*self.pending_selection)

    def clear(self, message="No image selected"):
        self.stop_scan()
        self.markers = {}
        self.labels = []
        self.pending_rows = []
        self.pending_selection = None
        super().clear(message)

    def load(self, path):
        if str(path) != self.path:
            self.clear()
        super().load(path)

    def set_markers(self, rows, visible=True):
        self.pending_rows, self.markers_visible = rows, visible
        for item in list(self.markers.values()) + self.labels:
            self.view.scene().removeItem(item)
        self.markers, self.labels = {}, []
        if not self.image_loaded:
            return
        bounds = self.view.sceneRect()
        for row in rows:
            if not row.get("bbox") or row.get("frame") not in {"normalized_reference", "full_page"}:
                continue
            marker = Marker(row, self.preview_scale, self.marker_clicked.emit)
            # Clip to actual display bounds; never invent a location for unlocalized findings.
            marker.setRect(marker.rect().intersected(bounds))
            if marker.rect().isEmpty():
                continue
            self.view.scene().addItem(marker)
            self.markers[row["id"]] = marker
            label = self.view.scene().addSimpleText(row["id"])
            label.setBrush(marker.pen().color())
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setPos(marker.rect().topLeft())
            label.setZValue(4)
            self.labels.append(label)
        self.show_markers(visible)

    def show_markers(self, visible):
        self.markers_visible = visible
        for item in list(self.markers.values()) + self.labels:
            item.setVisible(visible)

    def select_marker(self, identity, zoom=False):
        self.pending_selection = (identity, zoom)
        for key, marker in self.markers.items():
            marker.setSelected(key == identity)
            pen = QPen(marker.pen_style)
            pen.setWidth(4 if key == identity else 2)
            marker.setPen(pen)
        marker = self.markers.get(identity)
        if marker and zoom:
            self.view.fit_mode = False
            self.view.fitInView(marker.rect().adjusted(-45, -45, 45, 45), Qt.AspectRatioMode.KeepAspectRatio)

    def start_scan(self):
        self.stop_scan()
        self.scan_requested = True
        if self.path and self.image_loaded:
            self.phase = 0
            self.beam = self.view.scene().addRect(0, 0, 0, 0, QPen(Qt.PenStyle.NoPen))
            self.beam.setZValue(5)
            self.timer.start()

    def stop_scan(self):
        self.scan_requested = False
        self.timer.stop()
        if self.beam is not None:
            self.view.scene().removeItem(self.beam)
        self.beam = None

    def animate(self):
        if self.beam is None:
            return
        bounds = self.view.sceneRect()
        self.phase = (self.phase + .008) % 1
        y = bounds.height() * self.phase
        height = min(45, bounds.height() * .05)
        top, bottom = max(0, y - height), min(bounds.height(), y + height)
        self.beam.setRect(0, top, bounds.width(), bottom - top)
        gradient = QLinearGradient(0, top, 0, bottom)
        gradient.setColorAt(0, QColor(0, 210, 240, 0))
        gradient.setColorAt(.46, QColor(0, 210, 240, 40))
        gradient.setColorAt(.50, QColor(80, 235, 255, 210))
        gradient.setColorAt(.54, QColor(0, 210, 240, 40))
        gradient.setColorAt(1, QColor(0, 210, 240, 0))
        self.beam.setBrush(QBrush(gradient))
