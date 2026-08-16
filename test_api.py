import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)

def run_all_tests():
    print("\n--- [1] Testing Health Endpoint ---")
    res = client.get("/api/health")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    print(f"[OK] Health Check: {res.json()}")

    print("\n--- [2] Testing Categories Endpoint ---")
    res = client.get("/api/users/categories")
    assert res.status_code == 200
    categories = res.json()
    assert len(categories) >= 6
    print(f"[OK] Categories loaded: {len(categories)} topic categories found")

    print("\n--- [3] Testing Auth & Onboarding ---")
    auth_headers = {"Authorization": "Bearer mock-dr-elena"}
    
    res = client.post("/api/auth/verify", headers=auth_headers)
    assert res.status_code == 200
    user_profile = res.json()
    assert user_profile["uid"] == "dr-elena"
    print(f"[OK] Auth verification successful: {user_profile['display_name']} ({user_profile['uid']})")

    # Complete onboarding
    onboard_payload = {
        "interests": ["ai", "robotics", "design"],
        "bio": "AI Researcher building autonomous reasoning architectures."
    }
    res = client.post("/api/users/onboarding", json=onboard_payload, headers=auth_headers)
    assert res.status_code == 200
    updated_profile = res.json()
    assert updated_profile["onboarding_completed"] is True
    assert "ai" in updated_profile["interests"]
    print(f"[OK] Onboarding completed: Interests = {updated_profile['interests']}")

    print("\n--- [4] Testing Pulse Feed Pipeline ---")
    res = client.get("/api/feed?tab=for_you", headers=auth_headers)
    assert res.status_code == 200
    feed = res.json()
    assert "items" in feed
    assert len(feed["items"]) > 0
    print(f"[OK] For You Feed: {len(feed['items'])} items returned. Recommendation reason: '{feed['items'][0]['recommendation_reason']}'")

    res_trending = client.get("/api/feed?tab=trending")
    assert res_trending.status_code == 200
    assert len(res_trending.json()["items"]) > 0
    print(f"[OK] Trending Feed: Top item score = {res_trending.json()['items'][0]['idea']['stats']['ranking_score']}")

    res_latest = client.get("/api/feed?tab=latest")
    assert res_latest.status_code == 200
    print(f"[OK] Latest Feed: {len(res_latest.json()['items'])} items returned")

    print("\n--- [5] Testing Post Creation ---")
    post_payload = {
        "title": "Self-Evolving Codebases via Graph Neural Syntax Refinement",
        "text": "By representing repository code as abstract semantic graphs and training graph neural networks on commit histories, repositories can automatically suggest high-level architectural simplifications.",
        "topics": ["ai", "design"],
        "summary": "Graph neural syntax refinement for autonomous self-evolving codebases."
    }
    res = client.post("/api/posts", json=post_payload, headers=auth_headers)
    assert res.status_code == 201
    new_post = res.json()
    post_id = new_post["id"]
    print(f"[OK] Post created: ID={post_id}, Title='{new_post['title']}'")

    print("\n--- [6] Testing Multi-Reactions (Fire, Bulb, Like) ---")
    # React with Fire
    res = client.post(f"/api/posts/{post_id}/reactions", json={"reaction_type": "fire"}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["added"] is True
    assert res.json()["stats"]["fires"] == 1
    print("[OK] Added Fire reaction successfully")

    # React with Bulb
    res = client.post(f"/api/posts/{post_id}/reactions", json={"reaction_type": "bulb"}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["stats"]["bulbs"] == 1
    print("[OK] Added Bulb reaction successfully")

    print("\n--- [7] Testing Structured Discussion Threads ---")
    # Add Question comment
    comment_payload = {
        "text": "What is the token cost overhead compared to linear diffusion?",
        "comment_type": "question"
    }
    res = client.post(f"/api/posts/{post_id}/comments", json=comment_payload, headers=auth_headers)
    assert res.status_code == 201
    cmt_1 = res.json()
    print(f"[OK] Added Question Comment: ID={cmt_1['id']}")

    # Add Pro comment
    pro_payload = {
        "text": "Pro: AST-level transformations avoid hallucinated syntax errors.",
        "comment_type": "pro"
    }
    res = client.post(f"/api/posts/{post_id}/comments", json=pro_payload, headers=auth_headers)
    assert res.status_code == 201
    print("[OK] Added Pro Comment")

    # Fetch comments
    res = client.get(f"/api/posts/{post_id}/comments", headers=auth_headers)
    assert res.status_code == 200
    cmts = res.json()
    assert len(cmts) >= 2
    print(f"[OK] Retrieved {len(cmts)} structured comments successfully")

    print("\n--- [8] Testing Share & Related Posts ---")
    res = client.post(f"/api/posts/{post_id}/share")
    assert res.status_code == 200
    print(f"[OK] Post shared: new share count = {res.json()['shares_count']}")

    res = client.get(f"/api/posts/{post_id}/related")
    assert res.status_code == 200
    print(f"[OK] Related posts found: {len(res.json())} related ideas")

    print("\n==============================================")
    print(" ALL BACKEND API INTEGRATION TESTS PASSED!    ")
    print("==============================================\n")

if __name__ == "__main__":
    run_all_tests()
