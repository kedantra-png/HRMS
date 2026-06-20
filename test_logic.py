import sys, os
sys.path.append(os.getcwd())
from app import app, users, leave_class_allocations, hod_requests, get_classes_for_leave_period
import re
req = list(hod_requests.find())[-1]
staff_doc = users.find_one({'_id': req['requester_id']})
staff_id = staff_doc['staff_id']
original_classes = get_classes_for_leave_period(staff_id, req['leave_details']['from_date'], req['leave_details']['to_date'])
session = str(req.get('session', 'morning')).lower()
original_classes = [c for c in original_classes if str(c.get('period')) in ['0', 'I', 'II', 'III']]
allocs = list(leave_class_allocations.find({'assigned_by': str(req['requester_id']), 'status': {'\': ['accepted', 'approved', 'finalized']}}))
alloc_map = {}
for a in allocs:
    sub = str(a.get('class_details', {}).get('subject') or a.get('class_details', {}).get('class') or '').strip().upper()
    raw_key = f\
a.get('class_details', {}).get('date')
_
a.get('class_details', {}).get('time')
_
sub
\
    key = re.sub(r'\s+', '_', raw_key)
    alloc_map[key] = a
display_list = []
for c in original_classes:
    sub = str(c.get('subject') or c.get('class') or '').strip().upper()
    raw_key = f\
c.get('date')
_
c.get('time')
_
sub
\
    key = re.sub(r'\s+', '_', raw_key)
    alloc = alloc_map.get(key)
    display_list.append({'period': c.get('period'), 'status': alloc.get('status') if alloc else 'Pending'})
print(display_list)
