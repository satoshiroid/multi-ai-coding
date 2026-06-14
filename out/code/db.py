import sqlite3
import random

class Database:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file)
        self.create_table()

    def create_table(self):
        with self.conn:
            self.conn.execute('''CREATE TABLE IF NOT EXISTS words (
                                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  word TEXT UNIQUE NOT NULL)''')

    def add_word(self, word):
        with self.conn:
            self.conn.execute('INSERT INTO words (word) VALUES (?)', (word,))

    def list_words(self):
        cursor = self.conn.execute('SELECT word FROM words ORDER BY id')
        return [row[0] for row in cursor]

    def get_random_word(self):
        cursor = self.conn.execute('SELECT word FROM words ORDER BY RANDOM() LIMIT 1')
        row = cursor.fetchone()
        return row[0] if row else None