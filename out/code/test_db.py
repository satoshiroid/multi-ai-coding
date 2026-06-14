import unittest
import tempfile
import os
from db import Database
import sqlite3

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.db = Database(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_add_word(self):
        self.db.add_word('test')
        self.assertIn('test', self.db.list_words())

    def test_add_duplicate_word(self):
        self.db.add_word('duplicate')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_word('duplicate')

    def test_list_words(self):
        words = ['apple', 'banana', 'cherry']
        for word in words:
            self.db.add_word(word)
        self.assertEqual(self.db.list_words(), words)

    def test_get_random_word_empty(self):
        self.assertIsNone(self.db.get_random_word())

if __name__ == '__main__':
    unittest.main()