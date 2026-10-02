import sys
import sqlite3
import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QStackedWidget, QPushButton
)
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtCore import Qt, QSize


class ClickableLabel(QLabel):
    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.position().x() < self.width() / 2:
            self.on_click(-1)
        else:
            self.on_click(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("The-Dex")
        self.setFocusPolicy(Qt.StrongFocus)

        self.db = sqlite3.connect("thedex.db")
        self.db.execute("""CREATE TABLE IF NOT EXISTS continue_reading
            (manga_id TEXT PRIMARY KEY, title TEXT, chapter_id TEXT, chapter_number TEXT)""")
        self.db.commit()

        self.manga_data = []
        self.chapter_data = []
        self.page_urls = []
        self.current_page_index = 0
        self.page_cache = {}
        self.current_manga = None

        self.continue_list = QListWidget()
        self.continue_list.itemClicked.connect(self.resume_reading)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search manga...")
        self.search_bar.returnPressed.connect(self.search_manga)

        self.results_list = QListWidget()
        self.results_list.setIconSize(QSize(80, 120))
        self.results_list.itemClicked.connect(self.show_details)
        self.results_list.itemDoubleClicked.connect(self.show_chapters)
        self.results_list.hide()

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)

        search_layout = QVBoxLayout()
        search_layout.addWidget(self.search_bar)
        search_layout.addWidget(QLabel("Continue Reading"))
        search_layout.addWidget(self.continue_list)
        search_layout.addWidget(self.results_list)
        search_layout.addWidget(self.detail_label)
        search_page = QWidget()
        search_page.setLayout(search_layout)

        self.reader_image_label = ClickableLabel(self.change_page)
        back_button = QPushButton("Back to search")
        back_button.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        reader_layout = QVBoxLayout()
        reader_layout.addWidget(back_button)
        reader_layout.addWidget(self.reader_image_label)
        reader_page = QWidget()
        reader_page.setLayout(reader_layout)

        self.stack = QStackedWidget()
        self.stack.addWidget(search_page)
        self.stack.addWidget(reader_page)
        self.setCentralWidget(self.stack)

        self.load_continue_reading()

    def search_manga(self):
        query = self.search_bar.text()
        if not query:
            self.results_list.hide()
            self.continue_list.show()
            return

        self.results_list.clear()
        self.detail_label.clear()
        self.continue_list.hide()
        self.results_list.show()
        try:
            self.results_list.addItem("Searching...")
            QApplication.processEvents()

            response = requests.get(
                "https://api.mangadex.org/manga",
                params={
                    "title": query, "limit": 10,
                    "contentRating[]": "safe",
                    "includes[]": "cover_art"
                }
            )
            response.raise_for_status()
            self.manga_data = []
            self.results_list.clear()

            for manga in response.json()["data"]:
                attrs = manga["attributes"]
                title_dict = attrs["title"]
                title = title_dict.get("en") or next(iter(title_dict.values()), "Unknown title")
                self.manga_data.append({
                    "id": manga["id"],
                    "title": title,
                    "description": attrs["description"].get("en", "No description available."),
                })

                item = QListWidgetItem(title)
                cover_file = next((r["attributes"]["fileName"] for r in manga["relationships"]
                                    if r["type"] == "cover_art"), None)
                if cover_file:
                    cover_url = f"https://uploads.mangadex.org/covers/{manga['id']}/{cover_file}.256.jpg"
                    try:
                        cover_resp = requests.get(cover_url)
                        pixmap = QPixmap()
                        pixmap.loadFromData(cover_resp.content)
                        item.setIcon(QIcon(pixmap))
                    except requests.RequestException:
                        pass
                self.results_list.addItem(item)
                QApplication.processEvents()

            self.results_list.itemClicked.disconnect()
            self.results_list.itemClicked.connect(self.show_details)
        except requests.RequestException:
            self.results_list.clear()
            self.results_list.addItem("Search failed, check your connection")

    def show_details(self):
        index = self.results_list.currentRow()
        if 0 <= index < len(self.manga_data):
            self.detail_label.setText(self.manga_data[index]["description"])

    def show_chapters(self):
        index = self.results_list.currentRow()
        self.current_manga = self.manga_data[index]
        try:
            all_chapters = []
            offset = 0
            while True:
                response = requests.get(
                    f"https://api.mangadex.org/manga/{self.current_manga['id']}/feed",
                    params={"translatedLanguage[]": "en", "order[chapter]": "asc", "limit": 100, "offset": offset}
                )
                response.raise_for_status()
                data = response.json()
                all_chapters.extend(data["data"])
                offset += 100
                QApplication.processEvents()
                if offset >= data["total"]:
                    break

            seen_numbers = set()
            self.chapter_data = []
            self.results_list.clear()
            for chapter in all_chapters:
                attrs = chapter["attributes"]
                num = attrs["chapter"] or "?"
                if num in seen_numbers or attrs.get("pages", 0) == 0:
                    continue
                seen_numbers.add(num)
                self.chapter_data.append({"id": chapter["id"], "number": num})
                self.results_list.addItem(f"Chapter {num}")

            self.results_list.itemClicked.disconnect()
            self.results_list.itemClicked.connect(self.open_reader)
        except requests.RequestException:
            self.detail_label.setText("Failed to load chapters")

    def open_reader(self):
        index = self.results_list.currentRow()
        chapter = self.chapter_data[index]
        self.load_chapter(chapter["id"], chapter["number"])

    def resume_reading(self):
        index = self.continue_list.currentRow()
        row = self.continue_rows[index]
        self.current_manga = {"id": row[0], "title": row[1]}
        self.load_chapter(row[2], row[3])

    def load_chapter(self, chapter_id, chapter_number):
        try:
            response = requests.get(f"https://api.mangadex.org/at-home/server/{chapter_id}")
            response.raise_for_status()
            data = response.json()
            base_url = data["baseUrl"]
            chapter_hash = data["chapter"]["hash"]
            page_files = data["chapter"]["dataSaver"]

            if not page_files:
                self.detail_label.setText("No pages available for this chapter")
                return

            self.page_urls = [f"{base_url}/data-saver/{chapter_hash}/{f}" for f in page_files]
            self.current_page_index = 0
            self.page_cache = {}

            self.db.execute("""INSERT INTO continue_reading (manga_id, title, chapter_id, chapter_number)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(manga_id) DO UPDATE SET chapter_id=excluded.chapter_id, chapter_number=excluded.chapter_number""",
                (self.current_manga["id"], self.current_manga["title"], chapter_id, chapter_number))
            self.db.commit()
            self.load_continue_reading()

            self.stack.setCurrentIndex(1)
            self.setFocus()
            self.load_reader_page()
        except requests.RequestException:
            self.detail_label.setText("Failed to load chapter pages")

    def load_reader_page(self):
        url = self.page_urls[self.current_page_index]
        if url in self.page_cache:
            self.reader_image_label.setPixmap(self.page_cache[url])
            return
        try:
            response = requests.get(url)
            response.raise_for_status()
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            scaled = pixmap.scaled(780, 880, Qt.KeepAspectRatio)
            self.page_cache[url] = scaled
            self.reader_image_label.setPixmap(scaled)
        except requests.RequestException:
            self.reader_image_label.setText("Failed to load page")

    def change_page(self, direction):
        new_index = self.current_page_index + direction
        if 0 <= new_index < len(self.page_urls):
            self.current_page_index = new_index
            self.load_reader_page()

    def load_continue_reading(self):
        self.continue_list.clear()
        self.continue_rows = []
        cursor = self.db.execute("SELECT manga_id, title, chapter_id, chapter_number FROM continue_reading")
        for row in cursor.fetchall():
            self.continue_rows.append(row)
            self.continue_list.addItem(f"{row[1]} - Chapter {row[3]}")

    def keyPressEvent(self, event):
        if self.stack.currentIndex() != 1:
            return
        if event.key() == Qt.Key_Right:
            self.change_page(1)
        elif event.key() == Qt.Key_Left:
            self.change_page(-1)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QWidget { background-color: #1e1e1e; color: #e0e0e0; font-size: 13px; }
        QLineEdit, QListWidget { background-color: #2b2b2b; border: 1px solid #444; border-radius: 4px; padding: 4px; }
        QPushButton { background-color: #3a3a3a; border: 1px solid #555; border-radius: 4px; padding: 6px; }
        QPushButton:hover { background-color: #484848; }
    """)
    window = MainWindow()
    window.resize(500, 700)
    window.show()
    sys.exit(app.exec())