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
QPushButton#dangerButton { color: #B3261E; border-color: #F1C5C2; }
QPushButton#dangerButton:hover { background: #FCE8E6; border-color: #E6A5A0; }
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
QLabel#coverageWarning {
    color: #8A4B08;
    background: #FEF3E2;
    border: 1px solid #F2C779;
    border-radius: 10px;
    padding: 9px 11px;
    font-size: 9.4pt;
    font-weight: 600;
}
QLabel#coverageComplete {
    color: #137333;
    background: #E6F4EA;
    border: 1px solid #A8DAB5;
    border-radius: 10px;
    padding: 9px 11px;
    font-size: 9.4pt;
}
QLabel#coverageNeutral {
    color: #3C4043;
    background: #EEF3FD;
    border: 1px solid #D2E3FC;
    border-radius: 10px;
    padding: 9px 11px;
    font-size: 9.4pt;
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
QFrame#aiLaunchCard {
    background: #EEF4FF;
    border: 1px solid #C7D7F4;
    border-radius: 16px;
}
QFrame#chatSidebar {
    background: #F0F4FA;
    border: 1px solid #DCE3EE;
    border-radius: 14px;
}
QFrame#chatComposer {
    background: #FFFFFF;
    border: 1px solid #D2E3FC;
    border-radius: 14px;
}
QLabel#chatSectionTitle { font-size: 13pt; font-weight: 700; color: #174EA6; }
QLabel#chatBadge {
    color: #174EA6;
    background: #E8F0FE;
    border-radius: 9px;
    padding: 5px 9px;
    font-size: 9pt;
    font-weight: 700;
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
QTreeWidget, QTableWidget, QListWidget, QPlainTextEdit, QTextBrowser, QComboBox, QDateEdit {
    background: #FFFFFF;
    border: 1px solid #DADCE0;
    border-radius: 8px;
    selection-background-color: #D2E3FC;
    selection-color: #174EA6;
}
QTreeWidget, QTableWidget, QListWidget { alternate-background-color: #F8FAFD; }
QListWidget#conversationList, QListWidget#recentConversationList {
    padding: 5px;
    outline: none;
}
QListWidget#conversationList::item, QListWidget#recentConversationList::item {
    border-radius: 9px;
    padding: 9px;
    margin: 2px;
}
QListWidget#conversationList::item:selected, QListWidget#recentConversationList::item:selected {
    background: #D2E3FC;
    color: #174EA6;
}
QTextBrowser#chatTranscript {
    border: 1px solid #E1E5EB;
    border-radius: 14px;
    padding: 14px;
}
QTreeWidget#evidenceDrawer { background: #F8FAFD; border-color: #D2E3FC; }
QTreeWidget#deterministicTree {
    background: #FFFFFF;
    border: 1px solid #D2E3FC;
    border-radius: 12px;
    font-size: 9.4pt;
}
QPlainTextEdit#promptInspector {
    background: #111827;
    color: #E5E7EB;
    border: 1px solid #334155;
    border-radius: 11px;
    padding: 10px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
    font-size: 9.2pt;
}
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
QTabWidget#aiWorkspaceTabs::pane {
    background: #F8FAFD;
    border: 1px solid #DCE3EE;
    border-radius: 14px;
    top: -1px;
}
QTabWidget#aiWorkspaceTabs QTabBar::tab {
    padding: 9px 16px;
    margin-right: 4px;
}
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
