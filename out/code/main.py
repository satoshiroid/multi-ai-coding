from db import Database

def main():
    db = Database('words.db')
    db.add_word('example')
    print('Words in DB:', db.list_words())
    print('Random word:', db.get_random_word())

if __name__ == '__main__':
    main()