STYLE = """
QMainWindow, QWidget { background: #f4f6f8; color: #202d3a; font-family: 'Segoe UI'; font-size: 10pt; }
QGroupBox { font-weight: 600; border: 1px solid #d5dce3; border-radius: 6px; margin-top: 12px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QPushButton { background: white; border: 1px solid #bdc9d4; border-radius: 4px; padding: 7px 12px; }
QPushButton:hover { background: #e8eff7; }
QPushButton:disabled { color: #82909d; background: #e8ecf0; }
QPushButton#primary { background: #185fa5; color: white; font-weight: 600; }
QPushButton#primary:disabled { background: #9bafc4; }
QTableWidget, QPlainTextEdit, QGraphicsView { background: white; border: 1px solid #d5dce3; }
QHeaderView::section { background: #e8edf2; padding: 7px; border: none; border-bottom: 1px solid #ccd6df; }
QProgressBar { border: 1px solid #c5d0dc; border-radius: 4px; text-align: center; background: white; }
QProgressBar::chunk { background: #2976ba; }
QTabWidget::pane { border: 1px solid #d5dce3; }
QTabBar::tab { padding: 8px 10px; }
QTabBar::tab:selected { background: white; color: #185fa5; }
"""

STYLE += """
QMainWindow, QWidget { background: #eff3f7; color: #142e50; font-size: 10pt; }
QWidget#header { background: #133254; }
QWidget#header QLabel { background: transparent; color: white; }
QLabel#heading { font-size: 22pt; font-weight: 700; }
QLabel#subtitle { color: #b5c9df; font-size: 11pt; }
QWidget#header QPushButton { background: #203f61; color: white; border-color: #66809e; }
QWidget#panel { background: white; border: 1px solid #dce4ee; border-radius: 8px; }
QWidget#panel QLabel { background: transparent; border: none; }
QLabel#sectionTitle { font-size: 16pt; font-weight: 700; }
QLabel#muted { color: #6a7d98; }
QPushButton { padding: 9px 14px; border-radius: 6px; background: #f9fbfe; border: 1px solid #cedbea; }
QPushButton#primary { background: #008bed; color: white; border-color: #008bed; }
QPushButton#primary:disabled { background: #b0c5db; border-color: #b0c5db; }
QPushButton:focus, QComboBox:focus, QListWidget:focus { border: 2px solid #008bed; }
QListWidget { background: white; border: none; outline: 0; }
QListWidget::item { padding: 6px; border: 1px solid #e2e9f1; border-radius: 6px; }
QListWidget::item:selected { background: #dceffd; border: 1px solid #008bed; }
QGraphicsView { background: #e2e7ec; border: 1px solid #dbe3ed; border-radius: 5px; }
QComboBox { padding: 8px; min-width: 110px; background: white; border: 1px solid #cedbea; border-radius: 5px; }
QProgressBar { min-height: 16px; border: none; background: #dce4ed; }
QProgressBar::chunk { background: #008bed; border-radius: 4px; }
QSplitter::handle { background: #e1e8f0; }
QPlainTextEdit { padding: 6px; }
"""
