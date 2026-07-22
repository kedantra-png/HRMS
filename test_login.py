import requests
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings('ignore')

from pymongo import MongoClient
import bcrypt

client = MongoClient('mongodb://localhost:27017/')
db = client['hrms_db']
users = db['users']

hashed_pw = bcrypt.hashpw(b'test123', bcrypt.gensalt()).decode('utf-8')
users.update_one({'username': 'testuser'}, {'$set': {'password': hashed_pw, 'role': 'lecturer'}}, upsert=True)

session = requests.Session()
r = session.get('http://localhost:8000/login?role=lecturer')
soup = BeautifulSoup(r.text, 'html.parser')
csrf_token = soup.find('input', {'name': 'csrf_token'})['value']

data = {
    'csrf_token': csrf_token,
    'username': 'testuser',
    'password': 'test123',
    'remember': 'on'
}
r2 = session.post('http://localhost:8000/login', data=data, allow_redirects=False)
print('Status:', r2.status_code)
print('Cookies:', session.cookies.get_dict())
