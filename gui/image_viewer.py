from pathlib import Path
from PySide6.QtCore import Qt, QSize, QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QImageReader, QPixmap, QPainter, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel


class ZoomView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fit_mode = True

    def fit(self):
        self.fit_mode = True
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor):
        if 0.02 <= self.transform().m11() * factor <= 30:
            self.fit_mode = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        if self.fit_mode:
            self.fit()


class PreviewSignals(QObject):
    ready = Signal(int, object, float, str)


class PreviewJob(QRunnable):
    def __init__(self, path, token, signals):
        super().__init__()
        self.path, self.token, self.signals = path, token, signals

    def run(self):
        try:
            image, scale = read_preview(self.path)
            self.signals.ready.emit(self.token, image, scale, "")
        except Exception as exc:
            self.signals.ready.emit(self.token, None, 1, str(exc))


def read_preview(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.width() * size.height() > 40_000_000:
        raise ValueError("Image exceeds preview pixel limit")
    scale = min(1., 3000 / max(1, size.width(), size.height()))
    if scale < 1:
        reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
    image = reader.read()
    if image.isNull():
        from PIL import Image, ImageOps
        from PIL.ImageQt import ImageQt
        with Image.open(path) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Image exceeds preview pixel limit")
            original = ImageOps.exif_transpose(original).convert("RGBA")
            scale = min(1., 3000 / max(original.size))
            original.thumbnail((3000, 3000))
            image = ImageQt(original).copy()
    return image, 1 / scale


class ImageViewer(QWidget):
    loaded = Signal()
    def __init__(self, title=""):
        super().__init__()
        self.view = ZoomView()
        self.caption = QLabel(title)
        self.caption.setWordWrap(True)
        bar = QHBoxLayout()
        bar.addWidget(self.caption, 1)
        for label, callback in (("Fit", self.view.fit), ("−", lambda: self.view.zoom(1 / 1.25)),
                                ("+", lambda: self.view.zoom(1.25)), ("100%", self.actual_size)):
            button = QPushButton(label)
            button.setMaximumWidth(55)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.view)
        self.path = None
        self.preview_scale = 1.0
        self.token = 0
        self.image_loaded = False
        self.preview_rotation = 0
        self.source_preview = None
        self.preview_signals = PreviewSignals(self)
        self.preview_signals.ready.connect(self.preview_ready)

    def actual_size(self):
        self.view.fit_mode = False
        self.view.resetTransform()
        self.view.scale(self.preview_scale, self.preview_scale)

    def clear(self, message="No image selected"):
        self.token += 1
        self.image_loaded = False
        self.path = None
        self.source_preview = None
        self.preview_rotation = 0
        self.view.scene().clear()
        self.view.setSceneRect(0, 0, 0, 0)
        self.caption.setText(message)

    def load(self, path):
        if not path or not Path(path).is_file():
            self.clear("Image unavailable")
            return
        if str(path) == self.path:
            return
        self.clear("Loading preview…")
        self.path = str(path)
        QThreadPool.globalInstance().start(PreviewJob(str(path), self.token, self.preview_signals))

    def preview_ready(self, token, image, scale, error):
        if token != self.token:
            return
        if error:
            self.clear(f"Cannot preview image: {error}")
            return
        self.source_preview = image
        image = image.transformed(QTransform().rotate(self.preview_rotation), Qt.TransformationMode.FastTransformation)
        self.view.scene().clear()
        pixmap = self.view.scene().addPixmap(QPixmap.fromImage(image))
        self.view.setSceneRect(pixmap.boundingRect())
        self.preview_scale = scale
        self.caption.setText(Path(self.path).name)
        self.caption.setToolTip(self.path)
        self.view.fit()
        self.image_loaded = True
        self.loaded.emit()

    def load_source(self, path, angle=0):
        if hasattr(self, 'set_markers'):
            self.set_markers([])
        self.load(path)
        changed = self.preview_rotation != angle
        self.preview_rotation = angle
        if changed and self.source_preview is not None:
            self.preview_ready(self.token, self.source_preview, self.preview_scale, '')
