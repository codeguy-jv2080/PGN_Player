"""All UI tests use an offscreen Qt platform, never the live desktop."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTEST_QT_API"] = "pyside6"
