import os
import time
from pymongo import MongoClient

def auto_fix_mongo_and_aria():
    mongo_uri = os.getenv("DATABASE_URL") or os.getenv("MONGO_URL")
    if not mongo_uri:
        print("[AutoFix] ❌ No MongoDB URL found. Skipping cleanup.")
        return

    try:
        client = MongoClient(mongo_uri)
        db = client.get_database()
        col_names = db.list_collection_names()

        removed = 0
        for col in col_names:
            if "aria" in col.lower() or "task" in col.lower() or "download" in col.lower():
                result = db[col].delete_many({"status": {"$in": ["downloading", "error", "stalled"]}})
                removed += result.deleted_count

        print(f"[AutoFix] ✅ Cleaned {removed} stuck records in MongoDB.")

        # Clean old aria2 session files
        if os.path.exists("aria2.session"):
            os.remove("aria2.session")
            print("[AutoFix] 🔁 Removed old aria2.session file.")

        # Optional: reset DHT cache
        if os.path.exists(".aria2/dht.dat"):
            try:
                os.remove(".aria2/dht.dat")
                print("[AutoFix] 🔄 Reset DHT cache.")
            except Exception:
                pass

        # Write fresh aria2.session
        with open("aria2.session", "w") as f:
            f.write("# New session file created by AutoFix\n")

        print("[AutoFix] ✅ Aria2 auto-fix complete.")

    except Exception as e:
        print(f"[AutoFix] ⚠️ Error during cleanup: {e}")

