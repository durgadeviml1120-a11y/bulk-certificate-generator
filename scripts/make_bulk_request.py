"""Write a large request body for load testing:  python scripts/make_bulk_request.py 1000 > big.json"""
import json
import sys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
print(json.dumps({
    "course_name": "Load Test Course",
    "organization": "Acme Academy",
    "recipients": [{"name": f"Participant {i:05d}", "email": f"p{i}@example.com"} for i in range(n)],
}))
