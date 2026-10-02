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
