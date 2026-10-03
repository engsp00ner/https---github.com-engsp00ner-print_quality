"""Public entry point for the three-window desktop workflow."""
from .setup_window import MainWindow
from .dialogs import SettingsDialog, OrientationDialog

__all__ = ["MainWindow", "SettingsDialog", "OrientationDialog"]
