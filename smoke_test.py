import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = 30.0

# 1. Create room
r = httpx.post(f"{BASE}/api/rooms", json={"host_name": "Atul", "group_type": "friends"}, timeout=TIMEOUT)
assert r.status_code == 200, r.text
room = r.json()
code = room["room_code"]
host_id = room["user_id"]
print(f"Room created: {code}  host={room['user_name']}  id={host_id[:8]}")

# 2. Guest joins
r = httpx.post(f"{BASE}/api/rooms/{code}/join", json={"name": "Priya"}, timeout=TIMEOUT)
assert r.status_code == 200, r.text
guest = r.json()
guest_id = guest["user_id"]
print(f"Guest joined: {guest['user_name']}  id={guest_id[:8]}")
# 2b. Test notifications
print("Testing host notify player endpoint...")
r = httpx.post(f"{BASE}/api/users/{guest_id}/notify", timeout=TIMEOUT)
assert r.status_code == 200, r.text
assert r.json()["notified"] is True

# Verify notification flag in room details
r = httpx.get(f"{BASE}/api/rooms/{code}", timeout=TIMEOUT)
assert r.status_code == 200
details = r.json()
member_map = {m["id"]: m for m in details["members"]}
assert member_map[guest_id]["notified"] is True, "Expected guest to be flagged as notified"
print("Player successfully flagged as notified")

# 2c. Update user genres to shape recommendations
print("Updating user genre preferences...")
r = httpx.put(f"{BASE}/api/users/{host_id}/genres", json={"genres": ["Romance", "Drama"]}, timeout=TIMEOUT)
assert r.status_code == 200
r = httpx.put(f"{BASE}/api/users/{guest_id}/genres", json={"genres": ["Horror", "Action"]}, timeout=TIMEOUT)
assert r.status_code == 200
print("User genres updated successfully")

# 3. Room details (lobby state) - verify genres are returned and notified flag is cleared
r = httpx.get(f"{BASE}/api/rooms/{code}", timeout=TIMEOUT)
assert r.status_code == 200
details = r.json()
names = [m["name"] for m in details["members"]]
member_map = {m["id"]: m for m in details["members"]}
print(f"Room state={details['state']}  members={names}")
assert "Romance" in member_map[host_id]["genres"], "Expected host to have Romance genre preference"
assert "Horror" in member_map[guest_id]["genres"], "Expected guest to have Horror genre preference"
assert member_map[guest_id]["notified"] is False, "Expected guest notified flag to be cleared after setting vibe"
print("Member preferences and cleared notification state correctly returned in room details")

# 4. Start swiping
r = httpx.post(f"{BASE}/api/rooms/{code}/start", timeout=TIMEOUT)
assert r.status_code == 200, r.text
print(f"Session started: {r.json()}")

# 5. Fetch deck
r = httpx.get(f"{BASE}/api/rooms/{code}/recs", timeout=TIMEOUT)
assert r.status_code == 200, r.text
deck = r.json()
print(f"Deck size: {len(deck)}")

# 6. Test invalid vote validation
r = httpx.post(f"{BASE}/api/rooms/{code}/vote",
               json={"user_id": host_id, "tmdb_id": "invalid-tmdb-id-999", "choice": True}, timeout=TIMEOUT)
assert r.status_code == 400, f"Expected 400 on invalid vote, got {r.status_code}"
print("Invalid vote correctly rejected with 400")

# 7. Simulating dynamic reveal logic:
# Let's vote on 10 movies:
# - Movie 0: YES / YES (Unanimous match)
# - Movies 1-9: NO / NO (Not a match)
# Once host and guest both vote on Movie 9, it should auto-reveal because voted_by_all_count == 10 and unanimous_match_count == 1.
print("Casting votes on 10 movies to verify auto-reveal rule...")

# Vote YES on movie 0
for uid in [host_id, guest_id]:
    r = httpx.post(f"{BASE}/api/rooms/{code}/vote",
                   json={"user_id": uid, "tmdb_id": deck[0]["tmdb_id"], "choice": True}, timeout=TIMEOUT)
    assert r.status_code == 200

# Vote NO on movies 1-9
for card in deck[1:10]:
    for uid in [host_id, guest_id]:
        r = httpx.post(f"{BASE}/api/rooms/{code}/vote",
                       json={"user_id": uid, "tmdb_id": card["tmdb_id"], "choice": False}, timeout=TIMEOUT)
        assert r.status_code == 200

# 8. Verify that room state is now automatically "revealed"
r = httpx.get(f"{BASE}/api/rooms/{code}", timeout=TIMEOUT)
assert r.status_code == 200
details = r.json()
print(f"Room state after 10 votes (with 1 match): {details['state']}")
assert details["state"] == "revealed", f"Expected 'revealed' state, got {details['state']}"

# 9. Verify matches response
r = httpx.get(f"{BASE}/api/rooms/{code}/matches", timeout=TIMEOUT)
assert r.status_code == 200
result = r.json()
print(f"Matches count: {len(result['matches'])}  total_members={result['total_members']}")
assert len(result["matches"]) == 1, f"Expected 1 match, got {len(result['matches'])}"
top = result["matches"][0]
print(f"TOP MATCH: {top['title']}  yes={top['yes_count']}  unanimous={top['is_unanimous']}")
assert top["is_unanimous"] is True, "Expected top match to be unanimous"

print()
print("ALL DYNAMIC REVEAL AND RECOMMENDATION TESTS PASSED")
