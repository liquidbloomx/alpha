from pymongo import MongoClient
import os

# ✅ Auto-load your Mongo URL from environment
MONGO_URL = os.getenv("DATABASE_URL") or os.getenv("MONGODB_URI")
if not MONGO_URL:
    raise SystemExit("❌ DATABASE_URL or MONGODB_URI not set in environment.")

mongo = MongoClient(MONGO_URL)
db = mongo.get_default_database()

# ✅ Try to detect your BOT_ID automatically
config_doc = db.settings.config.find_one()
BOT_ID = config_doc["_id"] if config_doc and "_id" in config_doc else input("Enter your BOT_ID: ").strip()

numeric_fields = [
    "BOT_MAX_TASKS",
    "USER_MAX_TASKS",
    "QUEUE_ALL",
    "QUEUE_DOWNLOAD",
    "QUEUE_UPLOAD",
    "USER_TIME_INTERVAL",
]

doc = db.settings.config.find_one({"_id": BOT_ID})
if not doc:
    raise SystemExit(f"❌ No config found for BOT_ID = {BOT_ID}")

updated = {}
for f in numeric_fields:
    val = doc.get(f)
    if isinstance(val, str):
        try:
            updated[f] = int(float(val))
        except Exception:
            updated[f] = 0

if updated:
    db.settings.config.update_one({"_id": BOT_ID}, {"$set": updated})
    print(f"✅ Updated numeric fields for BOT_ID {BOT_ID}: {updated}")
else:
    print("⚙️ No fields needed fixing — all OK already.")
  
