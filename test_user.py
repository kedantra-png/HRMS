from pymongo import MongoClient
import bcrypt

client = MongoClient('mongodb://localhost:27017/')
db = client['hrms']
users = db['users']

hashed_pw = bcrypt.hashpw(b'test123', bcrypt.gensalt()).decode('utf-8')
users.update_one({'username': 'testuser'}, {'$set': {'password': hashed_pw, 'role': 'lecturer'}}, upsert=True)
