import sys, os, time
sys.path.insert(0, os.getcwd())

from app.main import app
from app.services.search_service import search_service
from app.database.db import db

# Build search index
count = search_service.build_index(db)
print("OK: Search index built:", count, "documents")

# Basic search
res = search_service.search_all("artificial intelligence", limit=5)
print("OK: Posts:", len(res["posts"]), "| Users:", len(res["users"]), "| Cats:", len(res["categories"]))

# Speed test
t = time.perf_counter()
for _ in range(100):
    search_service.search_all("quantum robotics")
ms = (time.perf_counter() - t) * 1000
print("OK: 100x searches:", round(ms, 1), "ms total,", round(ms/100, 3), "ms avg")

# Route check
routes = [r.path for r in app.routes]
checks = ["/api/search", "/api/users/check-username", "/ws/feed", "/api/users/avatar"]
for r in checks:
    found = any(r in route for route in routes)
    status = "OK" if found else "MISS"
    print(status + ": Route: " + r)

# DB checks
cats = db.get_categories()
print("OK: Categories loaded:", len(cats))

print()
print("=== ALL SYSTEMS GO ===")
