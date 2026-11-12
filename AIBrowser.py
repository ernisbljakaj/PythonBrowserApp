import sys
import threading

import keyring
import pyperclip
from openai import OpenAI
from pynput import keyboard
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

SERVICE = "browser-writing-assistant"
DEFAULT_MODEL = "gpt-6-astra"

class Events(QObject):
    