from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImageReader, QPixmap, QPainter
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


class ImageViewer(QWidget):
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

    def actual_size(self):
        self.view.fit_mode = False
        self.view.resetTransform()
        self.view.scale(self.preview_scale, self.preview_scale)

    def clear(self, message="No image selected"):
        self.path = None
        self.view.scene().clear()
        self.view.setSceneRect(0, 0, 0, 0)
        self.caption.setText(message)

    def load(self, path):
        if not path or not Path(path).is_file():
            self.clear("Image unavailable")
            return
        if str(path) == self.path:
            return
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        # Bound preview memory; saved inspection artifacts retain full resolution.
        scale = min(1., 3000 / max(1, size.width(), size.height()))
        if scale < 1:
            reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
        image = reader.read()
        if image.isNull():
            # Qt builds may omit TIFF plugins. Pillow is part of the required environment.
            from PIL import Image, ImageOps
            from PIL.ImageQt import ImageQt
            try:
                with Image.open(path) as original:
                    original = ImageOps.exif_transpose(original).convert("RGBA")
                    scale = min(1., 3000 / max(original.size))
                    original.thumbnail((3000, 3000))
                    image = ImageQt(original).copy()
            except (OSError, ValueError) as exc:
                self.clear(f"Cannot preview image: {exc}")
                return
        self.view.scene().clear()
        pixmap = self.view.scene().addPixmap(QPixmap.fromImage(image))
        self.view.setSceneRect(pixmap.boundingRect())
        self.path = str(path)
        self.preview_scale = 1 / scale
        self.caption.setText(Path(path).name)
        self.caption.setToolTip(str(path))
        self.view.fit()
