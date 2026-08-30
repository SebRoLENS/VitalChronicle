from __future__ import annotations

APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #F6F8FC;
    color: #202124;
    font-family: "Noto Sans", "Inter", sans-serif;
    font-size: 10.5pt;
}
QToolBar {
    background: #FFFFFF;
    border: none;
    border-bottom: 1px solid #E1E5EB;
    spacing: 7px;
    padding: 8px 12px;
}
QToolButton, QPushButton {
    background: #FFFFFF;
    border: 1px solid #D5DAE2;
    border-radius: 9px;
    padding: 7px 13px;
    color: #202124;
    font-weight: 600;
}
QToolButton:hover, QPushButton:hover { background: #EEF3FD; border-color: #AECBFA; }
QToolButton:pressed, QPushButton:pressed { background: #DDE8FC; }
QToolButton:disabled, QPushButton:disabled { color: #9AA0A6; background: #F1F3F4; }
QPushButton#primaryButton {
    background: #1A73E8;
    color: white;
    border: none;
    padding: 9px 16px;
}
QPushButton#primaryButton:hover { background: #1765CC; }
QLabel#appTitle { font-size: 18pt; font-weight: 700; color: #174EA6; }
QLabel#pageTitle { font-size: 20pt; font-weight: 700; color: #202124; }
QLabel#pageSubtitle { color: #5F6368; font-size: 11pt; }
QLabel#cardTitle { color: #5F6368; font-weight: 600; }
QLabel#cardValue { color: #202124; font-size: 22pt; font-weight: 700; }
QLabel#cardCaption { color: #5F6368; }
QLabel#progressBaseline { color: #5F6368; font-size: 9.5pt; }
QLabel#progressRatio { min-width: 44px; }
QLabel#sparklineCaption { color: #80868B; font-size: 8.7pt; }
QLabel#aiInterval {
    color: #174EA6;
    background: #E8F0FE;
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 9.3pt;
}
QLabel#overviewPill {
    color: #174EA6;
    background: #D2E3FC;
    border-radius: 10px;
    padding: 5px 10px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#disclaimer {
    color: #5F6368;
    background: #EEF3FD;
    border-radius: 9px;
    padding: 9px;
}
QFrame#metricCard, QFrame#chartCard, QFrame#aiCard, QFrame#statCard {
    background: #FFFFFF;
    border: 1px solid #E1E5EB;
    border-radius: 14px;
}
QFrame#thinkingCard, QFrame#answerCard {
    background: #F8FAFD;
    border: 1px solid #D2E3FC;
    border-radius: 12px;
}
QFrame#answerCard { background: #FFFFFF; }
QLabel#thinkingLive {
    color: #137333;
    background: #E6F4EA;
    border-radius: 8px;
    padding: 3px 8px;
    font-size: 8.5pt;
    font-weight: 700;
}
QFrame#overviewHero {
    background: #EAF2FF;
    border: 1px solid #D2E3FC;
    border-radius: 16px;
}
QTreeWidget, QTableWidget, QPlainTextEdit, QTextBrowser, QComboBox, QDateEdit {
    background: #FFFFFF;
    border: 1px solid #DADCE0;
    border-radius: 8px;
    selection-background-color: #D2E3FC;
    selection-color: #174EA6;
}
QTreeWidget, QTableWidget { alternate-background-color: #F8FAFD; }
QHeaderView::section {
    background: #F1F3F4;
    color: #5F6368;
    border: none;
    border-bottom: 1px solid #DADCE0;
    padding: 7px;
    font-weight: 600;
}
QComboBox, QDateEdit { padding: 6px 10px; }
QTabWidget::pane { border: none; }
QTabBar::tab {
    background: transparent;
    color: #5F6368;
    padding: 11px 18px;
    border-bottom: 3px solid transparent;
    font-weight: 600;
}
QTabBar::tab:selected { color: #1A73E8; border-bottom-color: #1A73E8; }
QProgressBar { border: none; background: #E8EAED; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #1A73E8; border-radius: 5px; }
QStatusBar { background: #FFFFFF; border-top: 1px solid #E1E5EB; }
QSplitter::handle { background: #E8EAED; width: 1px; }
"""
